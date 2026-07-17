#!/usr/bin/env python3
"""Cabby - AI Taxi Booking Agent powered by SignalWire."""

import os
import sys
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
from signalwire import AgentBase, AgentServer
from signalwire.core.function_result import SwaigFunctionResult

import config
from database import Database
from signalwire.skills.google_maps.skill import GoogleMapsClient

load_dotenv()

logging.basicConfig(level=logging.INFO, force=True)
logger = logging.getLogger(__name__)

# Initialize database and Google Maps client
db = Database(config.DATABASE_PATH)
db.initialize()
maps = GoogleMapsClient(config.GOOGLE_MAPS_API_KEY)
config.validate()


class CabbyAgent(AgentBase):
    """Cabby - AI Taxi Dispatcher"""

    def __init__(self):
        super().__init__(name="Cabby", route="/swml",
                         record_call=True, record_format="wav", record_stereo=True)

        # AI model
        self.set_param("ai_model", config.AI_MODEL)
        self.set_prompt_llm_params(top_p=config.AI_TOP_P, temperature=config.AI_TEMPERATURE)

        # Personality
        self.prompt_add_section("Personality",
            "You are Cabby, a friendly and efficient AI taxi dispatcher. "
            "You help callers book taxi rides, rebook previous trips, reverse trips, "
            "and cancel bookings. You are warm, concise, and professional."
        )

        # Hard rules for voice behavior and tool discipline
        self.prompt_add_section("Rules", body="", bullets=[
            "This is a PHONE CALL. Keep every response to 1-2 short sentences. Never give long lists of options.",
            "Ask ONE question at a time. Let the caller answer before moving on.",
            "Never include meta-instructions, stage directions, or parenthetical notes like '(awaiting response)' in your speech. Only say words the caller should hear.",
            "NEVER make up or guess addresses, fares, distances, or trip details. You MUST call the tools to get this information.",
            "NEVER skip calling a tool. You cannot validate an address without calling validate_address. You cannot get a fare without calling calculate_fare.",
            "ONLY reference data you can actually see in the prompt sections above. If there is no Recent Trips section, do NOT offer rebook or reverse. If there is no Pending Trip section, do NOT mention pending trips.",
            "Each step has ONE job. Do that job and nothing else. Do NOT ask about the destination during pickup. Do NOT discuss fares during address collection.",
            "You ONLY help with booking, rebooking, reversing, or cancelling taxi rides and managing saved addresses. If the caller asks about anything else — trivia, general knowledge, chitchat, or questions unrelated to their ride — politely redirect: 'I can only help with taxi bookings. Would you like to book a ride?'",
        ])

        # Voice
        self.add_language("English", "en-US", "azure.en-US-CoraMultilingualNeural")
        self.add_hints(["Cabby", "rebook", "reverse", "cancel", "pickup",
                        "destination", "fare", "home", "work"])

        # Post-prompt (required so SDK emits post_prompt_url for summarize_conversation)
        self.set_post_prompt("Summarize the conversation.")

        # State machine
        self._define_state_machine()

        # SWAIG tools
        self._define_tools()

        # Per-call dynamic config — SDK creates an ephemeral copy of this
        # agent for each request, so POM/global_data never leak between calls
        self.set_dynamic_config_callback(self._per_call_config)

    def _define_state_machine(self):
        """Define conversation contexts and steps."""
        contexts = self.define_contexts()
        ctx = contexts.add_context("default")

        # GREETING
        greeting = ctx.add_step("greeting")
        greeting.add_section("Task", "Introduce yourself as Cabby and determine what the caller needs")
        greeting.add_bullets("Process", [
            "Introduce yourself: 'Hi, I'm Cabby, your AI taxi dispatcher!'",
            "Check is_new_caller: ${global_data.is_new_caller}",
            "NEW CALLER (is_new_caller is true): Ask 'What's your name?' and let them answer. Only call register_customer after the caller has told you their name.",
            "RETURNING CALLER (is_new_caller is false): Greet them by name, then ask what they need",
            "If you can see a Pending Trip section above: just say 'I see you have a pending ride' and ask if they'd like to keep it or cancel — do NOT read out the addresses or fare",
            "If you can see a Recent Trips section above: just ask if they'd like to rebook or reverse their last trip — do NOT read out the addresses",
            "Based on what the caller wants:",
            "  REBOOK or REVERSE: move to get_pickup",
            "  CANCEL: move to cancel_confirm",
            "  NEW RIDE: move to get_pickup",
            "  UPDATE ADDRESS: move to update_address",
        ])
        greeting.add_bullets("Do NOT", [
            "Do NOT offer rebook or reverse if there is no Recent Trips section above",
            "Do NOT mention pending trips if there is no Pending Trip section above",
            "Do NOT read out addresses, fares, or trip details in the greeting — just acknowledge the ride exists and ask what they want to do",
            "Do NOT ask for addresses here — get_pickup handles that",
            "Do NOT call register_customer until the caller has actually said their name",
            "Do NOT list every option — just ask what they need",
        ])
        greeting.set_step_criteria("Caller's intent is determined")
        greeting.set_functions(["register_customer", "save_address", "cancel_booking"])
        greeting.set_valid_steps(["get_pickup", "cancel_confirm", "update_address", "wrap_up"])

        # GET PICKUP
        get_pickup = ctx.add_step("get_pickup")
        get_pickup.add_section("Task", "Collect the PICKUP address — where the taxi picks them up")
        get_pickup.add_bullets("Process", [
            "REBOOK: call validate_address with the pickup_address from the first Recent Trip",
            "REVERSE: call validate_address with the destination_address from the first Recent Trip (it becomes the pickup)",
            "If caller says 'home' or 'work': use the address from the Saved Addresses section",
            "Otherwise ask: 'Where would you like to be picked up?'",
            "Call validate_address with the address and address_type='pickup'",
            "After validate_address returns: say the address out loud and ask 'Is that correct?'",
            "If they say yes: move to get_destination",
            "If they say no: ask for the correct address and call validate_address again",
        ])
        get_pickup.add_bullets("Do NOT", [
            "Do NOT ask about the destination — that is the next step",
            "Do NOT skip calling validate_address — every address must be validated",
            "Do NOT move to get_destination until the caller confirms the pickup address",
        ])
        get_pickup.set_step_criteria("Pickup address validated and stored in booking_state")
        get_pickup.set_functions(["validate_address"])
        get_pickup.set_valid_steps(["get_destination"])

        # GET DESTINATION
        get_destination = ctx.add_step("get_destination")
        get_destination.add_section("Task", "Collect the DROP-OFF address — where the taxi takes them")
        get_destination.add_bullets("Process", [
            "REBOOK: call validate_address with the destination_address from the first Recent Trip",
            "REVERSE: call validate_address with the pickup_address from the first Recent Trip (it becomes the destination)",
            "If caller says 'home' or 'work': use the address from the Saved Addresses section",
            "Otherwise ask: 'And where are you headed?'",
            "Call validate_address with the address and address_type='destination'",
            "After validate_address returns: say the address out loud and ask 'Is that correct?'",
            "If they say yes: move to confirm_fare",
            "If they say no: ask for the correct address and call validate_address again",
        ])
        get_destination.add_bullets("Do NOT", [
            "Do NOT discuss fares or booking — that is the next step",
            "Do NOT skip calling validate_address — every address must be validated",
            "Do NOT move to confirm_fare until the caller confirms the destination address",
        ])
        get_destination.set_step_criteria("Destination address validated and stored in booking_state")
        get_destination.set_functions(["validate_address"])
        get_destination.set_valid_steps(["confirm_fare"])

        # CONFIRM FARE
        confirm_fare = ctx.add_step("confirm_fare")
        confirm_fare.add_section("Task", "Calculate fare and confirm booking")
        confirm_fare.add_bullets("Process", [
            "First call calculate_fare — it takes no parameters, it reads coordinates automatically",
            "Tell the caller the fare and travel time, then ask 'Shall I book it?'",
            "If yes: call confirm_booking (no parameters needed)",
            "If no: ask what they want to change, then move to get_pickup or get_destination",
        ])
        confirm_fare.add_bullets("Do NOT", [
            "Do NOT make up a fare — you MUST call calculate_fare to get the real number",
            "Do NOT call confirm_booking until the caller says yes",
        ])
        confirm_fare.set_step_criteria("Caller confirms or declines the booking")
        confirm_fare.set_functions(["calculate_fare", "confirm_booking"])
        confirm_fare.set_valid_steps(["offer_save_address", "wrap_up", "get_destination", "get_pickup"])

        # UPDATE ADDRESS
        update_address = ctx.add_step("update_address")
        update_address.add_section("Task", "Update the caller's saved home or work address")
        update_address.add_bullets("Process", [
            "Ask which address they want to update: 'home' or 'work'",
            "Ask for their new address",
            "Call validate_address with address_type='update' to verify it",
            "Read back the validated address and confirm with the caller",
            "If confirmed: call save_address with just the address_type ('home' or 'work')",
            "If wrong: ask for a corrected address and validate again"
        ])
        update_address.set_step_criteria("Address updated or caller declines")
        update_address.set_functions(["validate_address", "save_address"])
        update_address.set_valid_steps(["wrap_up", "greeting"])

        # CANCEL CONFIRM
        cancel_confirm = ctx.add_step("cancel_confirm")
        cancel_confirm.add_section("Task", "Cancel the pending booking")
        cancel_confirm.add_bullets("Process", [
            "Use the trip details from the Pending Trip section",
            "Confirm with the caller which trip to cancel",
            "Call cancel_booking to cancel it"
        ])
        cancel_confirm.set_step_criteria("Cancellation handled")
        cancel_confirm.set_functions(["cancel_booking"])
        cancel_confirm.set_valid_steps(["greeting", "wrap_up"])

        # OFFER SAVE ADDRESS — only reached when home or work is missing
        offer_save = ctx.add_step("offer_save_address")
        offer_save.add_section("Task", "Offer to save an address for next time")
        offer_save.add_bullets("Process", [
            "Tell the caller their ride is booked",
            "Ask: 'Would you like to save one of these addresses as home or work for next time?'",
            "If yes: call save_address with address_type 'home' or 'work'",
            "If no: move to wrap_up",
        ])
        offer_save.set_step_criteria("Caller accepts or declines saving an address")
        offer_save.set_functions(["save_address"])
        offer_save.set_valid_steps(["wrap_up"])

        # WRAP UP
        wrap_up = ctx.add_step("wrap_up")
        wrap_up.add_section("Task", "End the call")
        wrap_up.add_bullets("Process", [
            "Thank the caller warmly",
            "Wish them a safe trip",
            "Say goodbye",
            "If the caller says they need something else, move back to greeting"
        ])
        wrap_up.set_functions("none")
        wrap_up.set_valid_steps(["greeting"])

    def _define_tools(self):
        """Define all SWAIG tool functions."""

        # Helper functions
        def get_booking_state(raw_data):
            """Get current booking state from global_data."""
            global_data = raw_data.get('global_data', {})
            default = {
                "pickup": None,
                "destination": None,
                "route": None
            }
            return global_data.get('booking_state', default), global_data

        def save_booking_state(result, booking_state, global_data):
            """Save booking state back to global_data."""
            global_data['booking_state'] = booking_state
            result.update_global_data(global_data)
            return result

        # 1. REGISTER CUSTOMER
        @self.tool(
            name="register_customer",
            description="Register a new caller as a customer. Use when a new caller provides their name.",
            wait_file="/sounds/typing.mp3",
            fillers={"en-US": ["Let me get you registered.", "One moment while I set up your account."]},
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The caller's name"
                    }
                },
                "required": ["name"]
            }
        )
        def register_customer(args, raw_data):
            global_data = raw_data.get('global_data', {})

            # Already registered — don't re-register
            if global_data.get('customer'):
                customer_name = global_data['customer'].get('name', 'caller')
                return SwaigFunctionResult(
                    f"{customer_name} is already registered. Ask what they need — "
                    "a new ride, rebook, reverse, cancel, or update an address."
                )

            caller_phone = global_data.get('caller_phone', '')
            name = args.get("name", "").strip()

            if not name:
                return SwaigFunctionResult(
                    "I didn't catch the name. Ask the caller for their name again, "
                    "then call register_customer with the name."
                )

            try:
                customer = db.create_customer(caller_phone, name)
            except Exception as e:
                logger.error(f"register_customer DB error: {e}")
                customer = None

            if not customer:
                return SwaigFunctionResult(
                    "I had trouble with registration. Ask the caller for their name again "
                    "and call register_customer once more."
                )

            global_data['customer'] = {
                "id": customer["id"],
                "name": customer["name"],
                "phone": caller_phone,
                "home_address": customer.get("home_address"),
                "home_lat": customer.get("home_lat"),
                "home_lng": customer.get("home_lng"),
                "work_address": customer.get("work_address"),
                "work_lat": customer.get("work_lat"),
                "work_lng": customer.get("work_lng"),
            }
            global_data['is_new_caller'] = False
            global_data['customer_phone'] = caller_phone
            global_data.pop('caller_phone', None)

            result = SwaigFunctionResult(f"Welcome to Cabby, {name}! You're all registered. Now let's book you a ride.")
            result.update_global_data(global_data)
            result.swml_change_step("get_pickup")
            return result

        # 2. VALIDATE ADDRESS
        @self.tool(
            name="validate_address",
            description="Validate and geocode a street address or business name. "
                        "For destinations, the search is automatically biased toward the pickup area "
                        "so short queries like 'Walmart' find the nearest match.",
            wait_file="/sounds/typing.mp3",
            fillers={"en-US": ["Let me look that up.", "Checking that address.", "One moment."]},
            parameters={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "The address or business name to validate"
                    },
                    "address_type": {
                        "type": "string",
                        "description": "Whether this is a 'pickup', 'destination', or 'update' (for saving home/work)",
                        "enum": ["pickup", "destination", "update"]
                    }
                },
                "required": ["address", "address_type"]
            }
        )
        def validate_address(args, raw_data):
            address_input = args["address"]
            address_type = args["address_type"]
            logger.debug(f"SWAIG validate_address: address='{address_input}', type='{address_type}'")

            # When validating a destination, bias search toward the pickup location
            bias_lat = None
            bias_lng = None
            if address_type == "destination":
                booking_state_peek, _ = get_booking_state(raw_data)
                pickup = booking_state_peek.get("pickup")
                if pickup:
                    bias_lat = pickup.get("lat")
                    bias_lng = pickup.get("lng")
                    logger.debug(f"SWAIG validate_address: biasing toward pickup ({bias_lat}, {bias_lng})")
                else:
                    logger.debug(f"SWAIG validate_address: no pickup in booking_state, no bias")

            validated = maps.validate_address(address_input, bias_lat=bias_lat, bias_lng=bias_lng)
            if not validated:
                logger.debug(f"SWAIG validate_address: Google returned no results")
                return SwaigFunctionResult(
                    f"I couldn't find that address. Could you provide a more specific address?"
                )

            address = validated["address"]

            # Auto-detect label: Home/Work by matching saved addresses, or business name from Google
            global_data = raw_data.get('global_data', {})
            customer = global_data.get('customer') or {}
            label = ""
            home_addr = customer.get("home_address", "")
            work_addr = customer.get("work_address", "")
            if home_addr and address == home_addr:
                label = "Home"
            elif work_addr and address == work_addr:
                label = "Work"
            elif validated.get("business_name"):
                label = validated["business_name"]

            logger.debug(f"SWAIG validate_address: resolved to '{address}' "
                        f"({validated['lat']}, {validated['lng']}), auto_label='{label}'")

            if address_type == "update":
                # Store validated address in global_data for save_address to use
                global_data['validated_address'] = {
                    "address": address,
                    "lat": validated["lat"],
                    "lng": validated["lng"]
                }
                result = SwaigFunctionResult(
                    f"The address is {address}. "
                    f"Please confirm with the caller, then call save_address with the address_type."
                )
                result.update_global_data(global_data)
                return result

            booking_state, global_data = get_booking_state(raw_data)
            booking_state[address_type] = {
                "address": address,
                "lat": validated["lat"],
                "lng": validated["lng"],
                "label": label
            }

            if address_type == "pickup":
                result = SwaigFunctionResult(
                    f"The pickup address is {address}. "
                    f"Read this back to the caller. If they confirm it's correct, "
                    f"move to get_destination to collect the drop-off address."
                )
            elif address_type == "destination":
                result = SwaigFunctionResult(
                    f"The destination address is {address}. "
                    f"Read this back to the caller. If they confirm it's correct, "
                    f"move to confirm_fare to calculate the fare."
                )
            else:
                result = SwaigFunctionResult(
                    f"The {address_type} address is {address}. "
                    f"Read this back to the caller and confirm it's correct before continuing."
                )
            save_booking_state(result, booking_state, global_data)

            # No forced step transition — AI confirms address with caller first,
            # then moves to next step naturally via valid_steps

            return result

        # 3. CALCULATE FARE
        @self.tool(
            name="calculate_fare",
            description="Calculate the fare estimate using the pickup and destination already stored in booking_state. "
                        "No parameters needed — coordinates come from validate_address.",
            wait_file="/sounds/typing.mp3",
            fillers={"en-US": ["Let me calculate that fare.", "Checking the route and fare.", "One moment while I figure that out."]},
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def calculate_fare(args, raw_data):
            booking_state, global_data = get_booking_state(raw_data)
            pickup = booking_state.get("pickup")
            destination = booking_state.get("destination")

            if not pickup or not destination:
                logger.debug(f"SWAIG calculate_fare: missing booking_state — pickup={pickup}, dest={destination}")
                return SwaigFunctionResult(
                    "I need both a pickup and destination address first."
                )

            logger.debug(f"SWAIG calculate_fare: {pickup['address']} -> {destination['address']}")
            logger.debug(f"SWAIG calculate_fare: pickup=({pickup['lat']},{pickup['lng']}), "
                        f"dest=({destination['lat']},{destination['lng']})")

            route = maps.compute_route(
                pickup["lat"], pickup["lng"],
                destination["lat"], destination["lng"]
            )
            if not route:
                logger.debug(f"SWAIG calculate_fare: Routes API returned no route")
                return SwaigFunctionResult(
                    "I couldn't calculate the route. Please verify the addresses."
                )

            distance_miles = route["distance_meters"] / 1609.344
            duration_min = route["duration_seconds"] / 60.0

            # Reject trips that exceed max distance
            if distance_miles > config.MAX_DISTANCE_MILES:
                logger.debug(f"SWAIG calculate_fare: REJECTED — {distance_miles:.1f} mi exceeds "
                            f"max {config.MAX_DISTANCE_MILES} mi")
                return SwaigFunctionResult(
                    f"Sorry, that trip is {distance_miles:.0f} miles — we can only handle rides "
                    f"up to {int(config.MAX_DISTANCE_MILES)} miles. Could you choose a closer destination?"
                )

            fare = config.BASE_FARE + (distance_miles * config.PER_MILE_RATE) + (duration_min * config.PER_MINUTE_RATE)
            fare = max(fare, config.MINIMUM_FARE)
            fare = round(fare, 2)
            logger.debug(f"SWAIG calculate_fare: {distance_miles:.1f} mi, {duration_min:.0f} min, "
                        f"fare=${fare:.2f} (base=${config.BASE_FARE} + "
                        f"miles=${distance_miles * config.PER_MILE_RATE:.2f} + "
                        f"time=${duration_min * config.PER_MINUTE_RATE:.2f})")

            booking_state["route"] = {
                "distance_meters": route["distance_meters"],
                "duration_seconds": route["duration_seconds"],
                "fare_estimate": fare
            }

            result = SwaigFunctionResult(
                f"The estimated fare is ${fare:.2f} for a {distance_miles:.1f} mile trip, "
                f"about {int(duration_min)} minutes. Would you like to confirm this booking?"
            )
            save_booking_state(result, booking_state, global_data)
            return result

        # 4. CONFIRM BOOKING
        @self.tool(
            name="confirm_booking",
            description="Confirm and create the trip booking. Sends an SMS confirmation to the caller. "
                        "No parameters needed — all trip data comes from booking_state.",
            wait_file="/sounds/typing.mp3",
            fillers={"en-US": ["Booking your ride now.", "Let me confirm that for you.", "One moment while I finalize your booking."]},
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def confirm_booking(args, raw_data):
            global_data = raw_data.get('global_data', {})
            customer = global_data.get('customer')

            if not customer:
                return SwaigFunctionResult("I need to register you first before booking a trip.")

            # Use booking_state addresses (has labels from validate_address) over AI args
            booking_state, _ = get_booking_state(raw_data)
            bs_pickup = booking_state.get("pickup") or {}
            bs_dest = booking_state.get("destination") or {}
            bs_route = booking_state.get("route") or {}

            pickup_address = bs_pickup.get("address", "")
            pickup_lat = bs_pickup.get("lat", 0)
            pickup_lng = bs_pickup.get("lng", 0)
            pickup_label = bs_pickup.get("label", "")
            dest_address = bs_dest.get("address", "")
            dest_lat = bs_dest.get("lat", 0)
            dest_lng = bs_dest.get("lng", 0)
            dest_label = bs_dest.get("label", "")
            distance_meters = bs_route.get("distance_meters", 0)
            duration_seconds = bs_route.get("duration_seconds", 0)
            fare_estimate = bs_route.get("fare_estimate", 0)

            # Apply labels for display (SMS, trip record) — skip if address already starts with the label
            if pickup_label and not pickup_address.startswith(pickup_label):
                pickup_display = f"{pickup_label}, {pickup_address}"
            else:
                pickup_display = pickup_address
            if dest_label and not dest_address.startswith(dest_label):
                dest_display = f"{dest_label}, {dest_address}"
            else:
                dest_display = dest_address

            # Store labeled addresses so dashboard shows labels (e.g. "Home, 714 E Osage...")
            trip = db.create_trip(
                customer_id=customer["id"],
                pickup_address=pickup_display,
                pickup_lat=pickup_lat,
                pickup_lng=pickup_lng,
                destination_address=dest_display,
                destination_lat=dest_lat,
                destination_lng=dest_lng,
                distance_meters=distance_meters,
                duration_seconds=duration_seconds,
                fare_estimate=fare_estimate
            )

            if not trip:
                return SwaigFunctionResult("Sorry, I had trouble creating that booking. Let's try again.")

            duration_minutes = int(duration_seconds / 60)
            customer_name = customer.get("name", "")
            caller_phone = customer.get("phone", "")

            sms_body = (
                f"Cabby - Ride Confirmed!\n"
                f"Pickup: {pickup_display}\n"
                f"Destination: {dest_display}\n"
                f"Est. Fare: ${fare_estimate:.2f}\n"
                f"Est. Time: {duration_minutes} min\n"
                f"Thank you, {customer_name}!\n"
                f"Reply STOP to opt out."
            )

            result = SwaigFunctionResult(
                f"Your ride is booked! I've sent a confirmation text to your phone."
            )

            result.send_sms(
                to_number=caller_phone,
                from_number=config.SIGNALWIRE_PHONE_NUMBER,
                body=sms_body
            )

            # Update global_data with labeled addresses (matches DB and SMS)
            new_trip = {
                "id": trip["id"],
                "pickup_address": pickup_display,
                "pickup_lat": pickup_lat,
                "pickup_lng": pickup_lng,
                "destination_address": dest_display,
                "destination_lat": dest_lat,
                "destination_lng": dest_lng,
                "fare_estimate": fare_estimate,
                "status": "pending",
                "created_at": trip.get("created_at", "")
            }
            global_data['pending_trip'] = {
                "id": trip["id"],
                "pickup_address": pickup_display,
                "destination_address": dest_display,
                "fare_estimate": fare_estimate,
            }
            # Add to recent_trips so rebook data is available if caller books again
            recent = global_data.get('recent_trips', [])
            recent.insert(0, new_trip)
            global_data['recent_trips'] = recent[:5]
            # Only reset booking state if we're not offering to save an address
            result.update_global_data(global_data)

            # Offer to save address if home or work is missing, otherwise end call
            has_home = bool(customer.get("home_address"))
            has_work = bool(customer.get("work_address"))
            if has_home and has_work:
                result.swml_change_step("wrap_up")
            else:
                result.swml_change_step("offer_save_address")

            return result

        # 5. CANCEL BOOKING
        @self.tool(
            name="cancel_booking",
            description="Cancel the current pending trip booking.",
            wait_file="/sounds/typing.mp3",
            fillers={"en-US": ["Let me cancel that for you.", "One moment while I process the cancellation."]},
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def cancel_booking(args, raw_data):
            global_data = raw_data.get('global_data', {})
            pending = global_data.get('pending_trip')

            if not pending:
                return SwaigFunctionResult("You don't have any pending trips to cancel.")

            trip_id = pending["id"]
            db.cancel_trip(trip_id)

            global_data.pop('pending_trip', None)

            # Update status in recent_trips so it doesn't show as pending
            for t in global_data.get('recent_trips', []):
                if t.get("id") == trip_id:
                    t["status"] = "cancelled"
                    break

            customer = global_data.get('customer', {})
            customer_name = customer.get("name", "")
            caller_phone = customer.get("phone", "")

            sms_body = (
                f"Cabby - Ride Cancelled\n"
                f"Your ride from {pending['pickup_address']} "
                f"to {pending['destination_address']} has been cancelled.\n"
                f"Thank you, {customer_name}. Call us anytime to rebook!\n"
                f"Reply STOP to opt out."
            )

            result = SwaigFunctionResult(
                f"Your trip from {pending['pickup_address']} to "
                f"{pending['destination_address']} has been cancelled."
            )
            result.send_sms(
                to_number=caller_phone,
                from_number=config.SIGNALWIRE_PHONE_NUMBER,
                body=sms_body
            )
            result.update_global_data(global_data)
            result.swml_change_step("greeting")
            return result

        # 6. SAVE ADDRESS
        @self.tool(
            name="save_address",
            description="Save an address as the caller's home or work address for quick future bookings.",
            wait_file="/sounds/typing.mp3",
            fillers={"en-US": ["Saving that address.", "Let me save that for you."]},
            parameters={
                "type": "object",
                "properties": {
                    "address_type": {
                        "type": "string",
                        "description": "Whether to save as 'home' or 'work'",
                        "enum": ["home", "work"]
                    }
                },
                "required": ["address_type"]
            }
        )
        def save_address(args, raw_data):
            global_data = raw_data.get('global_data', {})
            customer = global_data.get('customer')

            if not customer:
                return SwaigFunctionResult("I need to register you first before saving addresses.")

            address_type = args["address_type"]

            # Pull address from validated_address (set by validate_address with type='update')
            # or from the current booking_state pickup/destination
            validated = global_data.get('validated_address')
            from_update = validated is not None
            if not validated:
                booking_state = global_data.get('booking_state', {})
                # Try pickup first, then destination
                validated = booking_state.get("pickup") or booking_state.get("destination")

            if not validated:
                return SwaigFunctionResult(
                    "I don't have a validated address to save. "
                    "Please call validate_address first."
                )

            address = validated["address"]
            lat = validated["lat"]
            lng = validated["lng"]

            db.update_customer_address(customer["id"], address_type, address, lat, lng)

            # Update global_data so it's immediately available
            customer[f"{address_type}_address"] = address
            customer[f"{address_type}_lat"] = lat
            customer[f"{address_type}_lng"] = lng
            global_data['customer'] = customer
            # Clean up stashed validated address
            global_data.pop('validated_address', None)

            result = SwaigFunctionResult(
                f"Saved {address} as your {address_type} address."
            )
            result.update_global_data(global_data)
            # Update flow → back to greeting; booking flow → end the call
            if from_update:
                result.swml_change_step("greeting")
            else:
                result.swml_change_step("wrap_up")
            return result

        # 7. SUMMARIZE CONVERSATION (special name — auto-triggers SUMMARY_FUNCTION mode)
        @self.tool(
            name="summarize_conversation",
            description="Generate a structured call summary. Called automatically when the conversation ends.",
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief description of what happened during the call"
                    }
                },
                "required": ["summary"]
            }
        )
        def summarize_conversation(args, raw_data):
            global_data = raw_data.get('global_data', {})
            customer = global_data.get('customer') or {}
            booking_state = global_data.get('booking_state') or {}
            pending_trip = global_data.get('pending_trip')

            # Customer info
            name = customer.get('name', 'Unknown')
            phone = customer.get('phone',
                global_data.get('customer_phone',
                    global_data.get('caller_phone', 'Unknown')))

            # Pickup
            pickup = booking_state.get('pickup') or {}
            pickup_label = pickup.get('label', '')

            # Destination
            dest = booking_state.get('destination') or {}
            dest_label = dest.get('label', '')

            # Route / fare
            route = booking_state.get('route') or {}

            # Saved addresses
            home_addr = customer.get('home_address')
            work_addr = customer.get('work_address')

            # Build structured JSON summary
            summary = {
                "customer": {
                    "name": name,
                    "phone": phone
                },
                "summary": args.get('summary', 'N/A'),
                "trip": {},
                "booking": None,
                "saved_addresses": {}
            }

            if pickup.get('address'):
                trip = summary.setdefault("trip", {})
                trip["pickup"] = {
                    "address": pickup['address'],
                    "label": pickup_label or None
                }

            if dest.get('address'):
                trip = summary.setdefault("trip", {})
                trip["destination"] = {
                    "address": dest['address'],
                    "label": dest_label or None
                }

            if route.get('fare_estimate') is not None:
                trip = summary.setdefault("trip", {})
                trip["fare"] = route['fare_estimate']
                if route.get('distance_meters'):
                    trip["distance_miles"] = round(route['distance_meters'] / 1609.344, 1)
                if route.get('duration_seconds'):
                    trip["duration_minutes"] = int(route['duration_seconds'] / 60)

            if pending_trip:
                summary["booking"] = {
                    "status": "confirmed",
                    "trip_id": pending_trip.get('id')
                }

            if home_addr:
                summary["saved_addresses"]["home"] = home_addr
            if work_addr:
                summary["saved_addresses"]["work"] = work_addr

            return SwaigFunctionResult(json.dumps(summary))

    def _per_call_config(self, query_params, body_params, headers, agent):
        """Pre-populate customer data on an ephemeral agent copy.

        The SDK deep-copies the POM, global_data, hints, etc. into `agent`
        before calling this. All mutations here are per-request and never
        leak into the shared instance or other concurrent calls.
        """
        call_data = (body_params or {}).get("call", {})
        caller_phone = call_data.get("caller_id_number", "")

        customer = db.get_customer_by_phone(caller_phone)

        if customer:
            # RETURNING CALLER
            recent_trips = db.get_recent_trips(customer["id"], limit=5)
            pending_trip = db.get_pending_trip(customer["id"])

            # Build global_data — only include sections that have real data
            gdata = {
                "customer_name": customer["name"],
                "customer_phone": caller_phone,
                "customer": {
                    "id": customer["id"],
                    "name": customer["name"],
                    "phone": caller_phone,
                    "home_address": customer["home_address"],
                    "home_lat": customer["home_lat"],
                    "home_lng": customer["home_lng"],
                    "work_address": customer["work_address"],
                    "work_lat": customer["work_lat"],
                    "work_lng": customer["work_lng"],
                },
                "is_new_caller": False,
                "booking_state": {
                    "pickup": None,
                    "destination": None,
                    "route": None
                }
            }

            # Only include recent_trips if there are any
            if recent_trips:
                gdata["recent_trips"] = [
                    {
                        "id": t["id"],
                        "pickup_address": t["pickup_address"],
                        "pickup_lat": t["pickup_lat"],
                        "pickup_lng": t["pickup_lng"],
                        "destination_address": t["destination_address"],
                        "destination_lat": t["destination_lat"],
                        "destination_lng": t["destination_lng"],
                        "fare_estimate": t["fare_estimate"],
                        "status": t["status"],
                        "created_at": t["created_at"]
                    } for t in recent_trips
                ]

            # Only include pending_trip if one exists
            if pending_trip:
                gdata["pending_trip"] = {
                    "id": pending_trip["id"],
                    "pickup_address": pending_trip["pickup_address"],
                    "destination_address": pending_trip["destination_address"],
                    "fare_estimate": pending_trip["fare_estimate"],
                }

            agent.set_global_data(gdata)

            # Remove register_customer from greeting — caller is already known
            greeting_step = agent._contexts_builder.get_context("default").get_step("greeting")
            greeting_step.set_functions(["save_address", "cancel_booking"])

            # Agent-level sections visible across ALL steps.
            agent.prompt_add_section("Caller",
                f"This is a returning customer named {customer['name']} "
                f"calling from {caller_phone}. Greet them by name."
            )

            agent.prompt_add_section("Saved Addresses",
                "Home: ${global_data.customer.home_address}\n"
                "Work: ${global_data.customer.work_address}"
            )

            if recent_trips:
                agent.prompt_add_section("Recent Trips", "${global_data.recent_trips}")

            if pending_trip:
                agent.prompt_add_section("Pending Trip", "${global_data.pending_trip}")

        else:
            # NEW CALLER — only set what's needed, no empty placeholders
            agent.set_global_data({
                "customer": None,
                "is_new_caller": True,
                "caller_phone": caller_phone,
                "booking_state": {
                    "pickup": None,
                    "destination": None,
                    "route": None
                }
            })
            agent.prompt_add_section("New Caller",
                f"This is a new caller from {caller_phone}. Welcome them to Cabby and ask for their name. "
                "Only call register_customer after the caller tells you their name."
            )

    def _render_swml(self, call_id=None, modifications=None):
        """Override to dump the generated SWML to stderr for debugging."""
        swml = super()._render_swml(call_id, modifications)
        try:
            parsed = json.loads(swml) if isinstance(swml, str) else swml
            print(json.dumps(parsed, indent=2, default=str), file=sys.stderr)
        except Exception:
            print(swml, file=sys.stderr)
        return swml

    def on_summary(self, summary=None, raw_data=None):
        """Called when the post-prompt summary is received after the call ends."""
        if summary:
            logger.info(f"Call summary: {summary}")

        if raw_data:
            calls_dir = Path(__file__).parent / "calls"
            calls_dir.mkdir(exist_ok=True)
            call_id = raw_data.get("call_id", "unknown")
            out_path = calls_dir / f"{call_id}.json"
            try:
                out_path.write_text(json.dumps(raw_data, indent=2, default=str))
                logger.info(f"Saved call data to {out_path}")
            except Exception as e:
                logger.error(f"Failed to save call data: {e}")


def print_startup_url():
    """Print the full SWML URL with auth for easy copy/paste."""
    base = config.SWML_PROXY_URL_BASE
    if base:
        # Use proxy URL if configured
        base = base.rstrip("/")
    else:
        host = config.HOST if config.HOST != "0.0.0.0" else "localhost"
        base = f"http://{host}:{config.PORT}"

    user = config.SWML_BASIC_AUTH_USER
    password = config.SWML_BASIC_AUTH_PASSWORD

    if user and password:
        # Insert auth into URL: http://user:pass@host:port/swml
        scheme, rest = base.split("://", 1)
        url = f"{scheme}://{user}:{password}@{rest}/swml"
    else:
        url = f"{base}/swml"

    logger.info(f"SWML endpoint: {url}")


def create_server():
    """Create and configure the AgentServer."""
    server = AgentServer(host=config.HOST, port=config.PORT)
    server.register(CabbyAgent(), "/swml")

    @server.app.get("/api/phone")
    def get_phone():
        """Return the Cabby phone number for the dashboard."""
        return {
            "phone": config.SIGNALWIRE_PHONE_NUMBER,
            "display": config.DISPLAY_PHONE_NUMBER or config.SIGNALWIRE_PHONE_NUMBER
        }

    @server.app.get("/api/bookings")
    def get_bookings():
        """Return all trips with customer info for the dashboard."""
        with db._connect() as conn:
            rows = conn.execute(
                """SELECT t.id, t.pickup_address, t.destination_address,
                          t.distance_meters, t.duration_seconds, t.fare_estimate,
                          t.status, t.created_at, t.updated_at,
                          c.name AS customer_name, c.phone AS customer_phone
                   FROM trips t
                   JOIN customers c ON t.customer_id = c.id
                   ORDER BY t.created_at DESC"""
            ).fetchall()
            return {"bookings": [dict(r) for r in rows]}

    # Serve static files from web/ directory
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        server.serve_static_files(str(web_dir))

    print_startup_url()
    return server


server = create_server()
app = server.app

if __name__ == "__main__":
    server.run()
