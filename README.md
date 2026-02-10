# Cabby - AI Taxi Dispatcher

<p align="center">
  <img src="web/img/logo.png" alt="Cabby Logo" width="200">
</p>

Voice-powered AI taxi booking demo built with [SignalWire AI Agents SDK](https://github.com/signalwire/signalwire-agents). Callers dial a phone number and speak with Cabby, an AI dispatcher that books rides, rebooks previous trips, cancels bookings, updates saved addresses, and sends SMS confirmations. Google APIs handle address validation and route/fare calculation. SQLite stores all customer and trip data.

## Architecture

```
Caller --> SignalWire --> cabby.py (AgentServer + CabbyAgent)
                            |
                            ├── database.py   (SQLite: customers, trips)
                            ├── google_api.py  (Places + Routes API)
                            ├── config.py      (env var loader)
                            └── web/           (booking dashboard)
```

**Key design pattern**: `_per_call_config` (dynamic config callback) pre-populates the caller's profile, saved addresses, trip history, and pending bookings into `set_global_data()` *before* the AI speaks. No SWAIG function is needed for customer identification -- the conversation starts personalized from the first word.

## State Machine

Cabby uses a **Contexts & Steps** state machine to control conversation flow. SWAIG tool results drive all step transitions via `swml_change_step()` -- the AI never decides when to move between steps.

### Flow Diagram

All step transitions are forced by SWAIG tool results via `swml_change_step()`. The AI never decides when to change steps — tools drive the flow.

```
                               INCOMING CALL
                                    │
                            ┌───────┴────────┐
                            │_per_call_config│  Looks up caller by phone.
                            │                │  Loads profile, trips, pending
                            └───────┬────────┘  into global_data + prompt.
                                    │
                                    v
┌──────────────────────────────────────────────────────────────┐
│                          GREETING                            │
│  Tools: register_customer, save_address, cancel_booking      │<──────┐
│──────────────────────────────────────────────────────────────│       │
│                                                              │       │
│  New caller: ask name → register_customer ─ forced ──────> GET_PICKUP│
│                                                              │       │
│  Returning caller, choose one:                               │       │
│    ├── New ride / Rebook / Reverse ──────────────> GET_PICKUP│       │
│    ├── Cancel pending trip ──────────────> CANCEL_CONFIRM    │       │
│    ├── Update home/work ─────────────────> UPDATE_ADDRESS    │       │
│    └── All done ─────────────────────────> WRAP_UP           │       │
└──────────────────────────────────────────────────────────────┘       │
                                                                       │
              ══════════════ BOOKING PIPELINE ══════════════           │
                                                                       │
┌──────────────────────────────────────────────────────────────┐       │
│                         GET_PICKUP                           │       │
│  Tools: validate_address                                     │       │
│──────────────────────────────────────────────────────────────│       │
│                                                              │       │
│  "Where should we pick you up?"                              │       │
│  Rebook: uses last trip's pickup address                     │       │
│  Reverse: uses last trip's DESTINATION as pickup             │       │
│  Home/work: uses saved address                               │       │
│                                                              │       │
│  validate_address(type=pickup) ──── forced ──> GET_DESTINATION       │
└──────────────────────────────────────────────────────────────┘       │
                              │                                        │
                              v                                        │
┌──────────────────────────────────────────────────────────────┐       │
│                       GET_DESTINATION                        │       │
│  Tools: validate_address                                     │       │
│──────────────────────────────────────────────────────────────│       │
│                                                              │       │
│  "And where are you headed?"                                 │       │
│  Rebook: uses last trip's destination address                │       │
│  Reverse: uses last trip's PICKUP as destination             │       │
│  Home/work: uses saved address                               │       │
│  Destination search biased toward pickup location            │       │
│                                                              │       │
│  validate_address(type=destination) ── forced ──> CONFIRM_FARE       │
└──────────────────────────────────────────────────────────────┘       │
                              │                                        │
                              v                                        │
┌──────────────────────────────────────────────────────────────┐       │
│                        CONFIRM_FARE                          │       │
│  Tools: calculate_fare, confirm_booking                      │       │
│──────────────────────────────────────────────────────────────│       │
│                                                              │       │
│  calculate_fare (no params — reads coords from booking_state)│       │
│  "Est. fare: $X.XX, about Y minutes. Shall I book it?"       │       │
│                                                              │       │
│  Caller confirms:                                            │       │
│    confirm_booking ── forced ──> WRAP_UP                     │       │
│      (when both home + work addresses are saved)             │       │
│    confirm_booking ── forced ──> OFFER_SAVE_ADDRESS          │       │
│      (when home or work address is missing)                  │       │
│                                                              │       │
│  Caller declines:                                            │       │
│    AI moves to GET_PICKUP or GET_DESTINATION to change trip  │       │
└──────────────────────────────────────────────────────────────┘       │
                         │             │                               │
                         │             │                               │
           (both saved)  │             │  (missing one)                │
                         │             │                               │
                         │             v                               │
                         │  ┌──────────────────────────────────────┐   │
                         │  │         OFFER_SAVE_ADDRESS           │   │
                         │  │  Tools: save_address                 │   │
                         │  │──────────────────────────────────────│   │
                         │  │                                      │   │
                         │  │  "Save pickup or drop-off as home    │   │
                         │  │   or work for next time?"            │   │
                         │  │                                      │   │
                         │  │  Yes: save_address ─ forced ──> WRAP_UP  │
                         │  │  No:  AI moves to ────────────> WRAP_UP  │
                         │  └──────────────────────────────────────┘   │
                         │                    │                        │
                         v                    v                        │
┌──────────────────────────────────────────────────────────────┐       │
│                          WRAP_UP                             │       │
│  Tools: (none)                                               │       │
│──────────────────────────────────────────────────────────────│       │
│                                                              │       │
│  "Have a great ride! Goodbye."                               │       │
│  If caller needs something else ─────────────> GREETING ─────│───────┘
└──────────────────────────────────────────────────────────────┘


              ══════════════ SIDE FLOWS ══════════════

        Both loop back to GREETING so the caller can
        book a ride, do something else, or hang up.

┌──────────────────────────────────────────────────────────────┐
│                       CANCEL_CONFIRM                         │
│  Tools: cancel_booking                                       │
│──────────────────────────────────────────────────────────────│
│                                                              │
│  "Cancel your ride from {pickup} to {destination}?"          │
│  cancel_booking (sends SMS) ──── forced ──────────> GREETING │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                       UPDATE_ADDRESS                         │
│  Tools: validate_address, save_address                       │
│──────────────────────────────────────────────────────────────│
│                                                              │
│  "Which address — home or work? What's the new address?"     │
│  validate_address(type=update) — validates, no step change   │
│  AI reads back address, caller confirms                      │
│  save_address(type) ──── forced ──────────────────> GREETING │
└──────────────────────────────────────────────────────────────┘
```

### Transition Summary

Every forced transition at a glance:

| Tool | Forced Transition |
|------|-------------------|
| `register_customer` | → `get_pickup` |
| `validate_address(type=pickup)` | → `get_destination` |
| `validate_address(type=destination)` | → `confirm_fare` |
| `validate_address(type=update)` | *(none — stays in update_address)* |
| `calculate_fare` | *(none — stays in confirm_fare)* |
| `confirm_booking` (both addresses saved) | → `wrap_up` |
| `confirm_booking` (missing home or work) | → `offer_save_address` |
| `cancel_booking` | → `greeting` |
| `save_address` (from update flow) | → `greeting` |
| `save_address` (from booking flow) | → `wrap_up` |

### Step Reference

#### `greeting` -- Entry Point

| | |
|---|---|
| **Task** | Introduce yourself as Cabby and determine what the caller needs |
| **Functions** | `register_customer`, `save_address`, `cancel_booking` |
| **Valid steps** | `get_pickup`, `cancel_confirm`, `update_address`, `wrap_up` |

**Behavior:**
- New caller: asks for name, calls `register_customer` (forced transition to `get_pickup`)
- Returning caller: greets by name, offers options based on data present
- Rebook/reverse: routes to `get_pickup` (addresses re-validated through normal flow)
- Cancel: routes to `cancel_confirm`
- Update address: routes to `update_address`

#### `get_pickup` -- Collect Pickup Address

| | |
|---|---|
| **Task** | Collect the PICKUP address |
| **Functions** | `validate_address` |
| **Valid steps** | `get_destination` |

**Behavior:**
- For rebook: uses pickup from last trip. For reverse: uses destination from last trip as pickup
- "home"/"work" uses saved address
- All addresses go through `validate_address` (forced transition to `get_destination`)

#### `get_destination` -- Collect Drop-off Address

| | |
|---|---|
| **Task** | Collect the DROP-OFF address |
| **Functions** | `validate_address` |
| **Valid steps** | `confirm_fare` |

**Behavior:**
- For rebook: uses destination from last trip. For reverse: uses pickup from last trip as destination
- Destination search is biased toward pickup location
- All addresses go through `validate_address` (forced transition to `confirm_fare`)

#### `confirm_fare` -- Calculate & Confirm

| | |
|---|---|
| **Task** | Calculate fare and confirm booking |
| **Functions** | `calculate_fare`, `confirm_booking` |
| **Valid steps** | `offer_save_address`, `wrap_up`, `get_destination`, `get_pickup` |

**Behavior:**
- `calculate_fare` takes no parameters -- reads coordinates from `booking_state`
- `confirm_booking` routes to `wrap_up` if both home+work are saved, or `offer_save_address` if either is missing

#### `update_address` -- Update Saved Address

| | |
|---|---|
| **Task** | Update the caller's saved home or work address |
| **Functions** | `validate_address`, `save_address` |
| **Valid steps** | `wrap_up`, `greeting` |

**Behavior:**
- Asks which address to update (home/work) and the new address
- `validate_address` with `address_type='update'` validates without step transitions
- `save_address` saves and returns to `greeting` (so caller can book a ride or do more)

#### `cancel_confirm` -- Cancel Pending Trip

| | |
|---|---|
| **Task** | Cancel the pending booking |
| **Functions** | `cancel_booking` |
| **Valid steps** | `greeting`, `wrap_up` |

**Behavior:**
- `cancel_booking` cancels the trip, sends SMS, returns to `greeting` (caller can book a new ride)

#### `offer_save_address` -- Save Address Offer

| | |
|---|---|
| **Task** | Offer to save an address for next time |
| **Functions** | `save_address` |
| **Valid steps** | `wrap_up` |

**Behavior:**
- Only reached when home or work address is missing after a booking
- If accepted: `save_address` saves and transitions to `wrap_up`
- If declined: moves to `wrap_up`

#### `wrap_up` -- End Call

| | |
|---|---|
| **Task** | End the call |
| **Functions** | *(none)* |
| **Valid steps** | `greeting` |

**Behavior:**
- Thanks the caller, says goodbye
- If caller needs something else, can return to `greeting`

## SWAIG Tools

All step transitions are forced by tool results via `swml_change_step()`.

| # | Tool | Parameters | Transition | Description |
|---|------|-----------|------------|-------------|
| 1 | `register_customer` | `name` | → `get_pickup` | Register new caller in DB + global_data |
| 2 | `validate_address` | `address`, `address_type` (pickup/destination/update) | pickup→`get_destination`, destination→`confirm_fare`, update→*(none)* | Google Places lookup. Auto-detects Home/Work/business labels |
| 3 | `calculate_fare` | *(none -- reads from booking_state)* | *(none)* | Google Routes API. Fare = `max(BASE + miles*RATE + min*RATE, MINIMUM)`. Rejects trips over MAX_DISTANCE_MILES |
| 4 | `confirm_booking` | *(reads from booking_state)* | → `wrap_up` or `offer_save_address` | Creates trip in DB, sends SMS. Routes based on whether saved addresses are complete |
| 5 | `cancel_booking` | *(none -- uses pending_trip from global_data)* | → `greeting` | Cancels trip in DB, sends SMS, returns to greeting for new options |
| 6 | `save_address` | `address_type` (home/work) | → `greeting` (update flow) or `wrap_up` (booking flow) | Pulls address from `validated_address` or `booking_state`. No lat/lng params needed |

## Address Labels

Labels (Home, Work, business names) are handled automatically:

- **Home/Work**: `validate_address` compares the validated result against saved `home_address`/`work_address` in the customer record
- **Business names**: Google Places returns `business_name` for POIs (e.g. "Walmart Supercenter", "Alamo Liquor")
- **Labels are stored separately** in `booking_state.pickup.label` / `booking_state.destination.label`
- **DB stores clean addresses** (no labels). Labels are only applied for display in SMS and pending trip info

## SMS Templates

Messages are sent via SignalWire's built-in `send_sms` action from the number configured in `SIGNALWIRE_PHONE_NUMBER`.

### Booking Confirmation

```
Cabby - Ride Confirmed!
Pickup: {pickup_display}
Destination: {dest_display}
Est. Fare: ${fare_estimate}
Est. Time: {duration_minutes} min
Thank you, {customer_name}!
Reply STOP to opt out.
```

### Cancellation Confirmation

```
Cabby - Ride Cancelled
Your ride from {pickup_address} to {destination_address} has been cancelled.
Thank you, {customer_name}. Call us anytime to rebook!
Reply STOP to opt out.
```

### 10DLC Registration Notes

- **Brand**: Cabby AI Taxi Dispatcher
- **Use case**: Transactional -- ride booking confirmations and cancellation notices
- **Opt-in**: Customers opt in by calling the Cabby phone number and completing a booking through the voice AI agent
- **Opt-out**: Customers can request to stop receiving messages by calling Cabby
- **Message volume**: Low -- max 2 messages per booking (1 confirmation + 1 if cancelled)
- **All messages are initiated by customer action** (booking or cancelling a ride via phone call)

## Address Search Strategy

`validate_address` in `google_api.py` uses a two-tier search to handle both business names ("Walmart", "Alamo Liquor") and street addresses ("123 Main St").

### Pickup Search (no bias coordinates)

Uses **Google Places Autocomplete** with no location bias.

### Destination Search (pickup coordinates available)

```
Caller says "Walmart"
        │
        v
┌─────────────────────────────┐
│  1. Nearby Search           │
│     keyword = "Walmart"     │
│     location = pickup coords│
│     rankby = distance       │──── Found? ──> Return closest match
│                             │
└─────────────┬───────────────┘
              │ Not found
              v
┌─────────────────────────────┐
│  2. Autocomplete (fallback) │
│     input = "Walmart"       │
│     location = pickup coords│
│     radius = 50km (soft)    │──── Return best match
│     NO strictbounds         │
└─────────────────────────────┘
```

### Spoken Number Normalization

`google_api.py` includes `_normalize_spoken_numbers()` which converts speech-to-text number words to digits before address lookups:

- "Seven one four East Osage" → "714 East Osage"
- "Three oh five Main Street" → "305 Main Street"
- "Seven hundred fourteen" → "714"

## Pre-Population (`_per_call_config`)

The dynamic config callback looks up the caller *before the AI speaks*:

**Returning caller** -- loads into `global_data`:
- `customer` -- id, name, phone, home/work addresses with coordinates
- `customer_name`, `customer_phone` -- top-level scalars for POM expansion
- `recent_trips` -- last 5 trips (only if any exist)
- `pending_trip` -- current active booking (only if one exists)
- `booking_state` -- pickup/destination/route (initially null)
- Agent-level prompt sections: Caller greeting, Saved Addresses, Recent Trips, Pending Trip

**New caller** -- sets:
- `is_new_caller: true`
- `caller_phone` -- for use by `register_customer`
- Agent-level prompt section instructing AI to ask for name

## Global Data State

```
global_data = {
    "customer_name": "Sarah",
    "customer_phone": "+15551234567",
    "customer": {id, name, phone, home_address, home_lat, home_lng, work_address, work_lat, work_lng},
    "is_new_caller": false,
    "recent_trips": [{id, pickup_address, pickup_lat, ..., status, created_at}],  // only if trips exist
    "pending_trip": {id, pickup_address, destination_address, fare_estimate},      // only if pending
    "booking_state": {
        "pickup": {address, lat, lng, label} | null,
        "destination": {address, lat, lng, label} | null,
        "route": {distance_meters, duration_seconds, fare_estimate} | null
    },
    "validated_address": {address, lat, lng}  // transient, used by update_address flow
}
```

## Conversation Flows

### New Caller -- First Booking
```
Call in → _per_call_config (not found, is_new_caller=true)
→ [greeting] "Hi, I'm Cabby! What's your name?" → register_customer("Sarah") → forced to get_pickup
→ [get_pickup] validate_address(type=pickup) → forced to get_destination
→ [get_destination] validate_address(type=destination) → forced to confirm_fare
→ [confirm_fare] calculate_fare → "$32.50, 22 min" → confirm_booking + SMS
→ [offer_save_address] "Save as home or work?" → save_address → forced to wrap_up
→ [wrap_up] "Have a great ride!"
```

### Returning Caller -- Quick Book with Saved Addresses
```
Call in → _per_call_config (found Sarah, home + work loaded)
→ [greeting] "Hi Sarah! Want a ride from home to work?"
→ [get_pickup] validate_address(home address) → forced to get_destination
→ [get_destination] validate_address(work address) → forced to confirm_fare
→ [confirm_fare] calculate_fare → "$18.50, 15 min" → confirm_booking + SMS
→ forced to wrap_up (both addresses saved)
```

### Rebook Last Trip
```
Call in → _per_call_config (found, recent_trips loaded)
→ [greeting] "Rebook my last trip" → move to get_pickup
→ [get_pickup] validate_address(last trip's pickup) → forced to get_destination
→ [get_destination] validate_address(last trip's destination) → forced to confirm_fare
→ [confirm_fare] calculate_fare → confirm_booking + SMS → wrap_up
```

### Reverse Trip
```
→ [greeting] "I need a ride back" → move to get_pickup
→ [get_pickup] validate_address(last trip's DESTINATION as pickup) → forced to get_destination
→ [get_destination] validate_address(last trip's PICKUP as destination) → forced to confirm_fare
→ [confirm_fare] calculate_fare → confirm_booking + SMS → wrap_up
```

### Cancel Pending Trip
```
→ [greeting] "Cancel it"
→ [cancel_confirm] cancel_booking + SMS → forced to greeting
→ [greeting] "Would you like to book a new ride?"
```

### Update Saved Address
```
→ [greeting] "Update my home address"
→ [update_address] validate_address(type=update) → save_address(type=home) → forced to greeting
→ [greeting] "Anything else?"
```

## Call Recording & Logging

- All calls are recorded in WAV stereo format (`record_call=True`)
- SWML is dumped to stderr on every request via `_render_swml` override
- Post-prompt summaries are logged and full call JSON is saved to `calls/{call_id}.json`

## Database Schema

### `customers`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| phone | TEXT UNIQUE | E.164 format |
| name | TEXT | Customer name |
| home_address | TEXT | Saved home address |
| home_lat, home_lng | REAL | Home coordinates |
| work_address | TEXT | Saved work address |
| work_lat, work_lng | REAL | Work coordinates |
| created_at | TEXT | Timestamp |

### `trips`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| customer_id | INTEGER FK | References customers.id |
| pickup_address | TEXT | Pickup location (clean, no labels) |
| pickup_lat, pickup_lng | REAL | Pickup coordinates |
| destination_address | TEXT | Drop-off location (clean, no labels) |
| destination_lat, destination_lng | REAL | Drop-off coordinates |
| distance_meters | INTEGER | Route distance |
| duration_seconds | INTEGER | Route duration |
| fare_estimate | REAL | Calculated fare |
| status | TEXT | pending / confirmed / completed / cancelled |
| created_at, updated_at | TEXT | Timestamps |

## Project Structure

```
taxibooking/
├── cabby.py            # CabbyAgent: state machine, 6 SWAIG tools, dynamic config, entry point
├── database.py         # SQLite: schema init, all CRUD operations
├── google_api.py       # Google Places + Routes API client, spoken number normalization
├── config.py           # Env var loader with defaults and validation
├── requirements.txt    # signalwire-agents, requests, python-dotenv
├── .env.example        # Credential template
├── calls/              # Post-prompt call data saved as {call_id}.json
└── web/
    ├── index.html      # Booking dashboard (auto-refreshes)
    └── img/
        └── logo.png    # Cabby logo
```

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Fill in: SIGNALWIRE_*, GOOGLE_MAPS_API_KEY, DISPLAY_PHONE_NUMBER

# Run
python cabby.py
```

The server prints the full SWML endpoint URL (with auth) on startup. Point your SignalWire phone number's SWML webhook to that URL.

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `SIGNALWIRE_PROJECT_ID` | SignalWire project ID | |
| `SIGNALWIRE_TOKEN` | SignalWire API token | |
| `SIGNALWIRE_SPACE` | SignalWire space (e.g. yourspace.signalwire.com) | |
| `SIGNALWIRE_PHONE_NUMBER` | E.164 phone number for SMS from-number | |
| `DISPLAY_PHONE_NUMBER` | Vanity display number for dashboard (e.g. 1-385-GOCABBY) | |
| `SWML_BASIC_AUTH_USER` | Basic auth username for SWML endpoint | |
| `SWML_BASIC_AUTH_PASSWORD` | Basic auth password for SWML endpoint | |
| `SWML_PROXY_URL_BASE` | Public URL base if behind proxy/ngrok | |
| `GOOGLE_MAPS_API_KEY` | Google Maps API key (Places + Routes) | |
| `BASE_FARE` | Base fare amount | 3.00 |
| `PER_MILE_RATE` | Rate per mile | 2.40 |
| `PER_MINUTE_RATE` | Rate per minute | 0.30 |
| `MINIMUM_FARE` | Minimum fare floor | 8.00 |
| `MAX_DISTANCE_MILES` | Maximum allowed trip distance | 100 |
| `HOST` | Server bind address | 0.0.0.0 |
| `PORT` | Server port | 3000 |
| `DATABASE_PATH` | SQLite database file path | cabby.db |

## Dashboard

The web dashboard at `/` shows all bookings in real-time with:
- Status counts (pending, confirmed, completed, cancelled)
- Total revenue
- Filterable booking table
- Auto-refreshes every 30 seconds
- Clickable vanity phone number in the header
