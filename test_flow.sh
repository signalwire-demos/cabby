#!/usr/bin/env bash
# =============================================================================
# Cabby SWAIG Flow Test Harness
#
# Tests all functions and the full booking flow using swaig-test with
# persistent call state (--call-id).
#
# Usage:  ./test_flow.sh [--debug]
# =============================================================================

SWAIG="./venv/bin/swaig-test"
AGENT="cabby.py"
PASS=0
FAIL=0
CALL_ID="test-$(date +%s)"
DEBUG=false

# Parse flags
for arg in "$@"; do
    case "$arg" in
        --debug|-d) DEBUG=true ;;
    esac
done

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

# ── helpers ──────────────────────────────────────────────────────────────────

check() {
    local label="$1"
    local pattern="$2"
    # Compensate for multibyte chars (→ is 3 bytes but 1 column)
    local byte_len char_len pad
    byte_len=$(printf '%s' "$label" | wc -c)
    char_len=${#label}
    pad=$((57 + byte_len - char_len))
    printf "${CYAN}  %-${pad}s${NC}" "$label"
    if echo "$OUTPUT" | grep -qi "$pattern"; then
        printf "${GREEN}PASS${NC}\n"
        ((PASS++))
    else
        printf "${RED}FAIL${NC} — expected '%s'\n" "$pattern"
        echo "      Got: $(echo "$OUTPUT" | head -2)"
        ((FAIL++))
    fi
}

run() {
    # Print the command being run in debug mode
    if $DEBUG; then
        printf "\n${DIM}  ▸ swaig-test %s${NC}\n" "$*"
    fi
    # Run swaig-test, store output. Args passed directly.
    OUTPUT=$("$SWAIG" "$AGENT" "$@" 2>/dev/null) || true
    # Print full response in debug mode
    if $DEBUG; then
        echo "$OUTPUT" | while IFS= read -r line; do
            printf "${DIM}    %s${NC}\n" "$line"
        done
    fi
}

section() {
    printf "\n${YELLOW}━━ %s ━━${NC}\n" "$1"
}

# ── Global data payloads ─────────────────────────────────────────────────────

NEW_CALLER='{"global_data":{"customer":null,"is_new_caller":true,"caller_phone":"+15551234567","booking_state":{"pickup":null,"destination":null,"route":null}}}'

KNOWN_CALLER='{"global_data":{"customer":{"id":1,"name":"Brian","phone":"+15551234567","home_address":"714 E Osage Ave, McAlester, OK 74501, USA","home_lat":34.920979,"home_lng":-95.76325,"work_address":"100 S Main St, McAlester, OK 74501, USA","work_lat":34.933,"work_lng":-95.7697},"is_new_caller":false,"customer_phone":"+15551234567","booking_state":{"pickup":null,"destination":null,"route":null}}}'

WITH_PICKUP='{"global_data":{"customer":{"id":1,"name":"Brian","phone":"+15551234567","home_address":"714 E Osage Ave, McAlester, OK 74501, USA","home_lat":34.920979,"home_lng":-95.76325,"work_address":null,"work_lat":null,"work_lng":null},"is_new_caller":false,"customer_phone":"+15551234567","booking_state":{"pickup":{"address":"714 E Osage Ave, McAlester, OK 74501, USA","lat":34.920979,"lng":-95.76325,"label":""},"destination":null,"route":null}}}'

WITH_BOTH='{"global_data":{"customer":{"id":1,"name":"Brian","phone":"+15551234567","home_address":null,"home_lat":null,"home_lng":null,"work_address":null,"work_lat":null,"work_lng":null},"is_new_caller":false,"customer_phone":"+15551234567","booking_state":{"pickup":{"address":"714 E Osage Ave, McAlester, OK 74501, USA","lat":34.920979,"lng":-95.76325,"label":""},"destination":{"address":"Walmart Supercenter, 432 S George Nigh Expy, McAlester, OK 74501, USA","lat":34.9190324,"lng":-95.7404856,"label":"Walmart Supercenter"},"route":null}}}'

WITH_ROUTE='{"global_data":{"customer":{"id":1,"name":"Brian","phone":"+15551234567","home_address":null,"home_lat":null,"home_lng":null,"work_address":null,"work_lat":null,"work_lng":null},"is_new_caller":false,"customer_phone":"+15551234567","booking_state":{"pickup":{"address":"714 E Osage Ave, McAlester, OK 74501, USA","lat":34.920979,"lng":-95.76325,"label":""},"destination":{"address":"Walmart Supercenter, 432 S George Nigh Expy, McAlester, OK 74501, USA","lat":34.9190324,"lng":-95.7404856,"label":"Walmart Supercenter"},"route":{"distance_meters":3091,"duration_seconds":403,"fare_estimate":9.62}}}}'

WITH_PENDING='{"global_data":{"customer":{"id":1,"name":"Brian","phone":"+15551234567","home_address":"714 E Osage Ave, McAlester, OK 74501, USA","home_lat":34.920979,"home_lng":-95.76325,"work_address":null,"work_lat":null,"work_lng":null},"is_new_caller":false,"customer_phone":"+15551234567","booking_state":{"pickup":null,"destination":null,"route":null},"pending_trip":{"id":1,"pickup_address":"714 E Osage Ave, McAlester, OK 74501, USA","destination_address":"Walmart Supercenter, 432 S George Nigh Expy, McAlester, OK 74501, USA","fare_estimate":9.62},"recent_trips":[{"id":1,"status":"pending"}]}}'

WITH_VALIDATED='{"global_data":{"customer":{"id":1,"name":"Brian","phone":"+15551234567","home_address":null,"home_lat":null,"home_lng":null,"work_address":null,"work_lat":null,"work_lng":null},"is_new_caller":false,"customer_phone":"+15551234567","booking_state":{"pickup":null,"destination":null,"route":null},"validated_address":{"address":"200 S Main St, McAlester, OK 74501, USA","lat":34.9298,"lng":-95.7697}}}'

# =============================================================================
printf "\n${YELLOW}╔══════════════════════════════════════════════════════════╗${NC}\n"
printf "${YELLOW}║              Cabby SWAIG Flow Test Harness               ║${NC}\n"
printf "${YELLOW}╚══════════════════════════════════════════════════════════╝${NC}\n"
printf "  Call ID: ${CYAN}%s${NC}\n" "$CALL_ID"

# =============================================================================
section "1. register_customer"
# =============================================================================

run --raw --call-id "${CALL_ID}-reg-empty" --custom-data "$NEW_CALLER" \
    --exec register_customer --name ""
check "Empty name → ask again" "didn't catch\|name"

run --raw --call-id "${CALL_ID}-reg-known" --custom-data "$KNOWN_CALLER" \
    --exec register_customer --name "Brian"
check "Already registered → skip" "already registered"

run --raw --call-id "$CALL_ID" --custom-data "$NEW_CALLER" \
    --exec register_customer --name "Brian"
check "Register new caller" "Welcome.*Brian\|registered"
check "  → is_new_caller: false" '"is_new_caller": false'
check "  → customer in global_data" '"customer"'
check "  → change_step: get_pickup" "get_pickup"

# =============================================================================
section "2. validate_address — pickup"
# =============================================================================

run --raw --call-id "$CALL_ID" \
    --exec validate_address --address "714 E Osage, McAlester OK" --address_type pickup
check "Validate pickup address" "714 E Osage"
check "  → pickup in booking_state" '"pickup"'
check "  → lat/lng present" '"lat"'

# =============================================================================
section "3. validate_address — destination (biased)"
# =============================================================================

run --raw --call-id "${CALL_ID}-dest" --custom-data "$WITH_PICKUP" \
    --exec validate_address --address "Walmart" --address_type destination
check "Resolve 'Walmart' near pickup" "Walmart.*McAlester\|Walmart Supercenter"
check "  → destination in booking_state" '"destination"'
check "  → label includes Walmart" "Walmart"

# =============================================================================
section "4. validate_address — update flow"
# =============================================================================

run --raw --call-id "${CALL_ID}-upd" \
    --exec validate_address --address "200 S Main St, McAlester OK" --address_type update
check "Validate for update" "200 S Main St"
check "  → validated_address in global_data" '"validated_address"'

# =============================================================================
section "5. calculate_fare"
# =============================================================================

run --raw --call-id "${CALL_ID}-fare-empty" --exec calculate_fare
check "No addresses → error" "need both\|pickup and destination"

run --raw --call-id "${CALL_ID}-fare-nopickup" --custom-data '{"global_data":{"booking_state":{"pickup":null,"destination":{"address":"x","lat":0,"lng":0},"route":null}}}' \
    --exec calculate_fare
check "No pickup → error" "need both\|pickup and destination"

run --raw --call-id "${CALL_ID}-fare" --custom-data "$WITH_BOTH" \
    --exec calculate_fare
check "Calculate fare TUL→Walmart" "estimated fare\|\\\$"
check "  → route in booking_state" '"route"'
check "  → fare_estimate present" '"fare_estimate"'
check "  → distance_meters present" '"distance_meters"'

# =============================================================================
section "6. confirm_booking"
# =============================================================================

run --raw --call-id "${CALL_ID}-book-nocust" --exec confirm_booking
check "No customer → error" "register\|need to"

run --raw --call-id "${CALL_ID}-book" --custom-data "$WITH_ROUTE" \
    --exec confirm_booking
check "Booking confirmed" "ride is booked\|booked"
check "  → SMS sent" "send_sms"
check "  → pending_trip in global_data" '"pending_trip"'
check "  → recent_trips updated" '"recent_trips"'
check "  → change_step: offer_save_address" "offer_save_address"

# =============================================================================
section "7. save_address — from booking_state"
# =============================================================================

run --raw --call-id "${CALL_ID}-save-nocust" --exec save_address --address_type home
check "No customer → error" "register\|need to"

run --raw --call-id "${CALL_ID}-save-noaddr" --custom-data "$KNOWN_CALLER" \
    --exec save_address --address_type home
check "No validated address → error" "don't have\|validate_address"

run --raw --call-id "${CALL_ID}-save-home" --custom-data "$WITH_BOTH" \
    --exec save_address --address_type home
check "Save pickup as home" "Saved.*home"
check "  → home_address updated" '"home_address"'
check "  → change_step: wrap_up" "wrap_up"

# =============================================================================
section "8. save_address — from validated_address (update flow)"
# =============================================================================

run --raw --call-id "${CALL_ID}-save-work" --custom-data "$WITH_VALIDATED" \
    --exec save_address --address_type work
check "Save validated as work" "Saved.*work"
check "  → work_address updated" '"work_address"'
check "  → change_step: greeting" "greeting"

# =============================================================================
section "9. cancel_booking"
# =============================================================================

run --raw --call-id "${CALL_ID}-cancel-empty" --exec cancel_booking
check "No pending trip → error" "don't have\|no.*pending"

run --raw --call-id "${CALL_ID}-cancel" --custom-data "$WITH_PENDING" \
    --exec cancel_booking
check "Cancel pending trip" "cancelled"
check "  → SMS sent" "send_sms"
check "  → cancellation SMS has addresses" "Ride Cancelled"
check "  → recent_trips status: cancelled" '"cancelled"'
check "  → change_step: greeting" "greeting"

# =============================================================================
section "10. summarize_conversation"
# =============================================================================

run --raw --call-id "${CALL_ID}-sum" --custom-data "$WITH_ROUTE" \
    --exec summarize_conversation --summary "Brian booked a ride from home to Walmart."
check "Summary generated" "Brian booked"
check "  → customer info present" '"customer"'
check "  → trip pickup present" '"pickup"'
check "  → trip destination present" '"destination"'
check "  → fare in summary" '"fare"'

# =============================================================================
section "11. Full booking flow (end-to-end)"
# =============================================================================

# Step 1: Register
run --raw --call-id "${CALL_ID}-flow" --custom-data "$NEW_CALLER" \
    --exec register_customer --name "FlowTest"
check "Flow → register" "Welcome.*FlowTest\|registered"

# Step 2: Validate pickup
run --raw --call-id "${CALL_ID}-flow" \
    --exec validate_address --address "714 E Osage Ave, McAlester OK" --address_type pickup
check "Flow → validate pickup" "pickup address"

# Step 3: Validate destination (with pickup bias)
run --raw --call-id "${CALL_ID}-flow" \
    --exec validate_address --address "Walmart McAlester" --address_type destination
check "Flow → validate destination" "destination address"

# Step 4: Calculate fare (dynamic config resets global_data, so pass state)
run --raw --call-id "${CALL_ID}-flow" --custom-data "$WITH_BOTH" \
    --exec calculate_fare
check "Flow → calculate fare" "estimated fare\|\\\$"

# Step 5: Confirm booking
run --raw --call-id "${CALL_ID}-flow" --custom-data "$WITH_ROUTE" \
    --exec confirm_booking
check "Flow → booking confirmed" "ride is booked\|booked"

# =============================================================================
# Summary
# =============================================================================
printf "\n${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
TOTAL=$((PASS + FAIL))
printf "  Total:  %d\n" "$TOTAL"
printf "  ${GREEN}Passed: %d${NC}\n" "$PASS"
printf "  ${RED}Failed: %d${NC}\n" "$FAIL"
if [ "$FAIL" -eq 0 ]; then
    printf "\n${GREEN}All tests passed!${NC}\n\n"
else
    printf "\n${RED}%d test(s) failed.${NC}\n\n" "$FAIL"
    exit 1
fi
