# Cabby State Machine

## Flow Diagram

```
                         CALL STARTS
                             |
                     +-------v-------+
                     |   GREETING    |
                     |               |
                     | New caller:   |
                     |  register     |
                     | Returning:    |
                     |  what do you  |
                     |  need?        |
                     +---+-+-+-+-----+
                         | | | |
            +------------+ | | +-------------+
            |              | |               |
            v              | v               v
  +---------+----+         | +--------+---+  +---------------+
  | CANCEL       |         | | UPDATE     |  |  GET PICKUP   |
  | CONFIRM      |         | | ADDRESS    |  |               |
  |              |         | |            |  | validate_addr |
  | cancel_      |         | | validate_  |  | (pickup)      |
  | booking      |         | | address    |  +-------+-------+
  +----+-+-------+         | | save_addr  |          |
       | |                 | +-----+--+---+          v
       | |                 |       |  |      +-------+-------+
       | |                 |       |  |      | GET DEST      |
       | +--->GREETING     |       |  |      |               |
       |                   |       |  |      | validate_addr |
       |                   |       |  |      | (destination) |
       |                   |       |  |      +-------+-------+
       |                   |       |  |              |
       |                   |       |  |              v
       |                   |       |  |      +-------+-------+
       |                   |       |  |      | CONFIRM FARE  |
       |                   |       |  |      |               |
       |                   |       |  |      | calculate_    |
       |                   |       |  |      | fare          |
       |                   |       |  |      | confirm_      |
       |                   |       |  |      | booking       |
       |                   |       |  |      +--+-+--+--+----+
       |                   |       |  |         | |  |  |
       |                   |       |  |         | |  |  +-->GET PICKUP
       |                   |       |  |         | |  +----->GET DEST
       |                   |       |  |         | |
       |                   |       |  |         | v
       |                   |       |  |  +------+----------+
       |                   |       |  |  | OFFER SAVE ADDR |
       |                   |       |  |  |                 |
       |                   |       |  |  | save_address    |
       |                   |       |  |  +--------+--------+
       |                   |       |  |           |
       v                   v       v  v           v
  +----+-------------------+-------+--+-----------+--+
  |                    WRAP UP                        |
  |                                                   |
  |  (no tools)                                       |
  |  Say goodbye, or loop back to GREETING            |
  +---------------------------+-----------------------+
                              |
                              v
                         GREETING (if caller needs more)
```

## Steps, Tools & Transitions

| Step | Tools Available | Transitions To | Triggered By |
|---|---|---|---|
| **greeting** | `register_customer`\*, `save_address`, `cancel_booking` | get_pickup, cancel_confirm, update_address, wrap_up | Call start, or loop back from wrap_up / cancel / address update |
| **get_pickup** | `validate_address` | get_destination | Caller wants a new ride, rebook, or reverse |
| **get_destination** | `validate_address` | confirm_fare | Pickup confirmed |
| **confirm_fare** | `calculate_fare`, `confirm_booking` | offer_save_address, wrap_up, get_pickup, get_destination | Destination confirmed |
| **update_address** | `validate_address`, `save_address` | wrap_up, greeting | Caller wants to update home/work |
| **cancel_confirm** | `cancel_booking` | greeting, wrap_up | Caller wants to cancel pending trip |
| **offer_save_address** | `save_address` | wrap_up | Booking confirmed, home or work not saved yet |
| **wrap_up** | *(none)* | greeting | Conversation complete |

\* `register_customer` is **removed** for returning callers at runtime.

## Tool Reference

| Tool | Parameters | What It Does |
|---|---|---|
| `register_customer` | `name` | Creates customer in DB, sets global_data, moves to get_pickup |
| `validate_address` | `address`, `address_type` (pickup/destination/update) | Geocodes via Google Maps, stores in booking_state |
| `calculate_fare` | *(none — reads booking_state)* | Computes route + fare via Google Routes API |
| `confirm_booking` | *(none — reads booking_state)* | Creates trip in DB, sends SMS confirmation |
| `cancel_booking` | *(none — reads pending_trip)* | Cancels pending trip in DB, sends SMS |
| `save_address` | `address_type` (home/work) | Saves validated address to customer record |

## Forced Step Changes (via tool results)

Some tools force a step transition regardless of the AI's intent:

| Tool | Forces Step To | Condition |
|---|---|---|
| `register_customer` | get_pickup | Always (new customer registered) |
| `confirm_booking` | offer_save_address | Home or work address missing |
| `confirm_booking` | wrap_up | Both home and work saved |
| `cancel_booking` | greeting | Always |
| `save_address` | greeting | Called from update_address flow |
| `save_address` | wrap_up | Called from offer_save_address flow |

## Caller Type Differences

```
NEW CALLER                          RETURNING CALLER
----------                          ----------------
greeting tools:                     greeting tools:
  register_customer                   save_address
  save_address                        cancel_booking
  cancel_booking
                                    (register_customer removed)
prompt sections:
  New Caller                        prompt sections:
                                      Caller
                                      Saved Addresses
                                      Recent Trips (if any)
                                      Pending Trip (if any)
```
