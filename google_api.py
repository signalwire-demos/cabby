"""Google Places and Routes API client for Cabby."""

import json
import logging
import requests

logger = logging.getLogger(__name__)

# Spoken number words → numeric values
_WORD_TO_NUM = {
    'zero': 0, 'oh': 0, 'o': 0,
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
    'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
    'eighteen': 18, 'nineteen': 19,
    'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
}


def _normalize_spoken_numbers(text):
    """Convert spoken number words to digits for address lookups.

    Speech-to-text often transcribes street numbers as words:
      "Seven one four East Osage" → "714 East Osage"
      "Three oh five Main Street" → "305 Main Street"
      "Twenty three Elm"          → "23 Elm"
      "Twelve thirty four Oak"    → "1234 Oak"
      "Seven hundred fourteen"    → "714"
    Already-numeric input passes through unchanged.
    """
    words = text.split()
    tokens = []  # mix of ints (from number words) and strs (regular words)
    i = 0

    while i < len(words):
        w = words[i].lower().rstrip('.,')

        if w in _WORD_TO_NUM:
            val = _WORD_TO_NUM[w]

            # Tens + units: "twenty three" → 23
            if val >= 20 and val % 10 == 0 and i + 1 < len(words):
                nxt = words[i + 1].lower().rstrip('.,')
                if nxt in _WORD_TO_NUM and 1 <= _WORD_TO_NUM[nxt] <= 9:
                    val += _WORD_TO_NUM[nxt]
                    i += 1

            # Hundred: "seven hundred" → 700, "seven hundred fourteen" → 714
            if i + 1 < len(words) and words[i + 1].lower().rstrip('.,') == 'hundred':
                val *= 100
                i += 1
                if i + 1 < len(words):
                    nxt = words[i + 1].lower().rstrip('.,')
                    if nxt in _WORD_TO_NUM:
                        rem = _WORD_TO_NUM[nxt]
                        if rem >= 20 and rem % 10 == 0 and i + 2 < len(words):
                            nxt2 = words[i + 2].lower().rstrip('.,')
                            if nxt2 in _WORD_TO_NUM and 1 <= _WORD_TO_NUM[nxt2] <= 9:
                                rem += _WORD_TO_NUM[nxt2]
                                i += 1
                        val += rem
                        i += 1

            tokens.append(str(val))
            i += 1
        else:
            tokens.append(words[i])
            i += 1

    # Collapse adjacent digit tokens: ["7","1","4","East"] → ["714","East"]
    result = []
    num_buf = []
    for tok in tokens:
        if tok.isdigit():
            num_buf.append(tok)
        else:
            if num_buf:
                result.append(''.join(num_buf))
                num_buf = []
            result.append(tok)
    if num_buf:
        result.append(''.join(num_buf))

    return ' '.join(result)


def _debug_json(label, data):
    """Log a JSON payload at DEBUG level with pretty formatting."""
    logger.debug(f"{label}:\n{json.dumps(data, indent=2, default=str)}")


class GoogleMapsClient:
    def __init__(self, api_key):
        self.api_key = api_key

    def validate_address(self, input_text, bias_lat=None, bias_lng=None):
        """Validate and geocode an address or business name.

        When bias_lat/bias_lng are provided (destination search with known pickup):
        1. First tries Nearby Search with rankby=distance to find the CLOSEST
           matching business (e.g. "Walmart" → nearest Walmart to pickup).
        2. Falls back to Autocomplete with location bias (no strictbounds) for
           street addresses or if Nearby Search finds nothing.

        Without bias coords (pickup search): uses plain Autocomplete.

        Returns: {"address": str, "lat": float, "lng": float} or None
        """
        if not self.api_key:
            logger.error("Google Maps API key not configured")
            return None

        try:
            # Convert spoken number words to digits: "Seven one four" → "714"
            original = input_text
            input_text = _normalize_spoken_numbers(input_text)
            if input_text != original:
                logger.debug(f"Normalized spoken numbers: '{original}' → '{input_text}'")

            logger.debug(f"validate_address called: input='{input_text}', bias_lat={bias_lat}, bias_lng={bias_lng}")

            # When we have pickup coordinates, try Nearby Search first
            # to find the closest matching business
            if bias_lat is not None and bias_lng is not None:
                logger.debug(f"Trying Nearby Search first (bias coords available)")
                result = self._nearby_search(input_text, bias_lat, bias_lng)
                if result:
                    logger.debug(f"Nearby Search succeeded, returning result")
                    _debug_json("validate_address final result", result)
                    return result
                logger.debug(f"Nearby Search returned nothing, falling back to Autocomplete")

            # Fall back to Autocomplete (always used for pickup, and for
            # destinations when Nearby Search finds nothing — e.g. street addresses)
            result = self._autocomplete_search(input_text, bias_lat, bias_lng)
            _debug_json("validate_address final result", result)
            return result

        except Exception as e:
            logger.error(f"Address validation error: {e}")
            return None

    def _nearby_search(self, keyword, lat, lng):
        """Find the closest matching business using Nearby Search with rankby=distance.

        Only returns results whose name actually contains the search keyword,
        to avoid false matches on category alone (e.g. "Walmart" matching
        a random grocery store because they share the same place type).

        Returns: {"address": str, "lat": float, "lng": float} or None
        """
        url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
        params = {
            "keyword": keyword,
            "location": f"{lat},{lng}",
            "rankby": "distance",
            "key": self.api_key
        }

        logger.debug(f"Nearby Search request: keyword='{keyword}', location={lat},{lng}, rankby=distance")
        resp = requests.get(url, params=params)
        data = resp.json()
        logger.debug(f"Nearby Search status: {data.get('status')}, results count: {len(data.get('results', []))}")

        if data.get("status") != "OK" or not data.get("results"):
            _debug_json("Nearby Search response (no results)", data)
            return None

        # Filter to results whose name actually contains the keyword
        keyword_lower = keyword.lower()
        name_matches = [
            r for r in data["results"]
            if keyword_lower in r.get("name", "").lower()
        ]

        # Log top 3 raw results and match count for debugging
        for i, r in enumerate(data["results"][:3]):
            loc = r["geometry"]["location"]
            matched = keyword_lower in r.get("name", "").lower()
            logger.debug(f"  Nearby result #{i+1}: {r.get('name', '?')} - "
                        f"{r.get('vicinity', '?')} ({loc['lat']},{loc['lng']}) "
                        f"{'[NAME MATCH]' if matched else '[no match]'}")

        if not name_matches:
            logger.debug(f"Nearby Search: {len(data['results'])} results but none with '{keyword}' in name")
            return None

        logger.debug(f"Nearby Search: {len(name_matches)} name-matched results out of {len(data['results'])}")

        place = name_matches[0]  # Closest name-matched result by distance
        location = place["geometry"]["location"]
        name = place.get("name", "")
        vicinity = place.get("vicinity", "")
        logger.debug(f"Selected closest match: '{name}' at {vicinity} (place_id={place['place_id']})")

        # Get full formatted address via Place Details
        details = self._get_place_details(place["place_id"])
        if details:
            return details

        # If Place Details fails, use what Nearby Search gave us
        address = f"{name}, {vicinity}" if name else vicinity
        return {
            "address": address,
            "lat": location["lat"],
            "lng": location["lng"],
            "business_name": name
        }

    def _autocomplete_search(self, input_text, bias_lat=None, bias_lng=None):
        """Validate address using Places Autocomplete + Place Details.

        Returns: {"address": str, "lat": float, "lng": float} or None
        """
        autocomplete_url = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
        params = {
            "input": input_text,
            "key": self.api_key
        }
        # Soft bias toward pickup area (no strictbounds — far destinations still work)
        if bias_lat is not None and bias_lng is not None:
            params["location"] = f"{bias_lat},{bias_lng}"
            params["radius"] = 50000  # 50 km bias radius

        logger.debug(f"Autocomplete request: input='{input_text}', bias={bias_lat},{bias_lng}")
        ac_resp = requests.get(autocomplete_url, params=params)
        ac_data = ac_resp.json()
        logger.debug(f"Autocomplete status: {ac_data.get('status')}, predictions count: {len(ac_data.get('predictions', []))}")

        if ac_data.get("status") != "OK" or not ac_data.get("predictions"):
            _debug_json("Autocomplete response (no predictions)", ac_data)
            logger.warning(f"No autocomplete results for: {input_text}")
            return None

        # Log top 3 predictions for debugging
        for i, p in enumerate(ac_data["predictions"][:3]):
            logger.debug(f"  Autocomplete prediction #{i+1}: {p.get('description', '?')} (place_id={p['place_id']})")

        place_id = ac_data["predictions"][0]["place_id"]
        logger.debug(f"Selected prediction: place_id={place_id}")
        return self._get_place_details(place_id)

    def _get_place_details(self, place_id):
        """Get full address and coordinates from a place_id.

        Returns: {"address": str, "lat": float, "lng": float} or None
        """
        logger.debug(f"Place Details request: place_id={place_id}")
        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        det_resp = requests.get(details_url, params={
            "place_id": place_id,
            "fields": "name,formatted_address,geometry,types",
            "key": self.api_key
        })
        det_data = det_resp.json()
        logger.debug(f"Place Details status: {det_data.get('status')}")

        if det_data.get("status") != "OK":
            _debug_json("Place Details response (failed)", det_data)
            logger.warning(f"Place details failed for place_id: {place_id}")
            return None

        result = det_data["result"]
        location = result["geometry"]["location"]
        name = result.get("name", "")
        formatted = result["formatted_address"]
        types = result.get("types", [])

        logger.debug(f"Place Details: name='{name}', formatted='{formatted}', types={types}, "
                     f"lat={location['lat']}, lng={location['lng']}")

        # For businesses/POIs, prepend the name so the caller hears it
        is_business = any(t for t in types if t not in (
            "street_address", "route", "intersection", "premise",
            "subpremise", "postal_code", "locality",
            "administrative_area_level_1", "administrative_area_level_2",
            "country", "political"
        ))
        if is_business and name and not formatted.startswith(name):
            address = f"{name}, {formatted}"
            logger.debug(f"Business detected, prepending name: '{address}'")
        else:
            address = formatted

        return {
            "address": address,
            "lat": location["lat"],
            "lng": location["lng"],
            "business_name": name if is_business else ""
        }

    def compute_route(self, origin_lat, origin_lng, dest_lat, dest_lng):
        """Compute route using Google Routes API.

        Returns: {"distance_meters": int, "duration_seconds": int} or None
        """
        if not self.api_key:
            logger.error("Google Maps API key not configured")
            return None

        try:
            logger.debug(f"compute_route called: origin=({origin_lat},{origin_lng}), dest=({dest_lat},{dest_lng})")
            url = "https://routes.googleapis.com/directions/v2:computeRoutes"
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "routes.distanceMeters,routes.duration"
            }
            body = {
                "origin": {
                    "location": {
                        "latLng": {"latitude": origin_lat, "longitude": origin_lng}
                    }
                },
                "destination": {
                    "location": {
                        "latLng": {"latitude": dest_lat, "longitude": dest_lng}
                    }
                },
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_AWARE"
            }

            _debug_json("Routes API request body", body)
            resp = requests.post(url, json=body, headers=headers)
            data = resp.json()
            _debug_json("Routes API response", data)

            if "routes" not in data or not data["routes"]:
                logger.warning("No routes found")
                return None

            route = data["routes"][0]
            duration_str = route.get("duration", "0s")
            duration_seconds = int(duration_str.rstrip("s"))

            result = {
                "distance_meters": route.get("distanceMeters", 0),
                "duration_seconds": duration_seconds
            }
            distance_miles = result["distance_meters"] / 1609.344
            logger.debug(f"Route result: {distance_miles:.1f} miles, {duration_seconds/60:.0f} min")
            return result

        except Exception as e:
            logger.error(f"Route computation error: {e}")
            return None
