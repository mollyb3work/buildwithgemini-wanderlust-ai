# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import datetime
import os
import re
import uuid
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import requests
from google.cloud import firestore, storage
from google import genai

load_dotenv()

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types
from vertexai import agent_engines
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from .a2ui_utils import a2ui_callback


MODEL = "gemini-3.8-flash"
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-03-b19cec0b1d20"
STORAGE_BUCKET_NAME = "wanderlust-travel-media-qwiklabs-gcp-03-b19cec0b1d20"
SANDBOX_RESOURCE_NAME = "projects/28440506019/locations/us-east1/reasoningEngines/1829225609293725696/sandboxEnvironments/5741388036478861312"
AGENT_ENGINE_RESOURCE_NAME = "projects/28440506019/locations/us-east1/reasoningEngines/1829225609293725696"
MEMORY_BANK_ID = "1829225609293725696"


def get_firestore_client() -> firestore.Client:
    """Return a Firestore client configured with the hardcoded project ID."""
    return firestore.Client(project=FIRESTORE_PROJECT_ID)


def search_attractions(city: str = "", category: str = "", query: str = "") -> list[dict]:
    """Search travel attractions in the Firestore database.

    Args:
        city: Optional city filter (e.g. 'Tokyo', 'Paris', 'Taipei', 'Kyoto', 'New York').
        category: Optional category filter (e.g. 'Sightseeing', 'Culture', 'Museum', 'Nature').
        query: Optional keyword to match against attraction name, description, or tags.

    Returns:
        A list of matching attraction dictionaries.
    """
    db = get_firestore_client()
    collection = db.collection("attractions")
    docs = collection.stream()

    results = []
    city_lower = city.strip().lower()
    category_lower = category.strip().lower()
    query_lower = query.strip().lower()

    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id

        if city_lower and city_lower not in data.get("city", "").lower():
            continue
        if category_lower and category_lower not in data.get("category", "").lower():
            continue
        if query_lower:
            text = f"{data.get('name', '')} {data.get('description', '')} {' '.join(data.get('tags', []))}".lower()
            if query_lower not in text:
                continue
        results.append(data)

    return results


def get_attraction_details(attraction_id: str) -> dict:
    """Retrieve full details of a specific attraction by its ID.

    Args:
        attraction_id: The unique ID of the attraction (e.g. 'tokyo_shibuya_sky', 'paris_louvre').

    Returns:
        The attraction details dictionary or an error message if not found.
    """
    db = get_firestore_client()
    doc_ref = db.collection("attractions").document(attraction_id)
    doc = doc_ref.get()
    if not doc.exists:
        return {"error": f"Attraction with ID '{attraction_id}' was not found."}
    data = doc.to_dict()
    data["id"] = doc.id
    return data


def add_attraction(
    name: str,
    city: str,
    country: str,
    category: str,
    description: str,
    rating: float = 4.5,
    price_level: str = "$$",
    estimated_duration_hours: float = 2.0,
    tags: list[str] = [],
) -> dict:
    """Add a new travel attraction or point of interest to the Firestore database.

    Args:
        name: Name of the attraction (e.g. 'Senso-ji Temple').
        city: City where it is located.
        country: Country where it is located.
        category: Category (e.g. 'Sightseeing', 'Culture', 'Food', 'Nature').
        description: Informative description of what to see or do.
        rating: Average rating between 1.0 and 5.0.
        price_level: Price level indicator ('Free', '$', '$$', '$$$').
        estimated_duration_hours: Recommended visit duration in hours.
        tags: List of descriptive keyword tags.

    Returns:
        A confirmation dict containing the created attraction ID and details.
    """
    db = get_firestore_client()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", f"{city}_{name}").strip("_").lower()
    item = {
        "id": slug,
        "name": name,
        "city": city,
        "country": country,
        "category": category,
        "description": description,
        "rating": float(rating),
        "price_level": price_level,
        "estimated_duration_hours": float(estimated_duration_hours),
        "tags": tags or [],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    db.collection("attractions").document(slug).set(item)
    return {
        "status": "success",
        "message": f"Successfully added {name} to attractions catalog.",
        "attraction": item,
    }


def save_travel_itinerary(
    destination: str,
    title: str,
    days: int,
    itinerary_summary: str,
    notes: str = "",
) -> dict:
    """Save a planned itinerary into the Firestore database for future reference.

    Args:
        destination: Main city or region of the trip.
        title: Title of the itinerary.
        days: Number of days.
        itinerary_summary: Day-by-day plan or highlights summary.
        notes: Optional extra tips, packing suggestions, or notes.

    Returns:
        A confirmation dict with saved itinerary record ID.
    """
    db = get_firestore_client()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", f"itinerary_{destination}_{days}d").strip("_").lower()
    record = {
        "destination": destination,
        "title": title,
        "days": days,
        "itinerary_summary": itinerary_summary,
        "notes": notes,
        "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    db.collection("itineraries").document(slug).set(record)
    return {
        "status": "success",
        "itinerary_id": slug,
        "message": f"Saved itinerary '{title}' to Firestore.",
    }


def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert an amount from one currency to another using real-time foreign exchange rates.

    Args:
        amount: The numerical amount of money to convert (e.g. 1000.0).
        from_currency: 3-letter currency code of the source currency (e.g. 'USD', 'JPY', 'EUR', 'TWD', 'GBP').
        to_currency: 3-letter currency code of the target currency (e.g. 'TWD', 'USD', 'JPY', 'EUR').

    Returns:
        A dictionary with the converted amount, exchange rate, and formatted string.
    """
    base = from_currency.strip().upper()
    target = to_currency.strip().upper()
    try:
        resp = requests.get(f"https://open.er-api.com/v6/latest/{base}", timeout=5)
        data = resp.json()
        if data.get("result") != "success":
            return {"error": f"Failed to fetch exchange rates for base currency '{base}'."}

        rates = data.get("rates", {})
        if target not in rates:
            return {"error": f"Target currency '{target}' not supported or found."}

        rate = rates[target]
        converted = round(amount * rate, 2)
        return {
            "amount": amount,
            "from_currency": base,
            "to_currency": target,
            "exchange_rate": rate,
            "converted_amount": converted,
            "formatted": f"{amount:,.2f} {base} = {converted:,.2f} {target} (Rate: 1 {base} = {rate:.4f} {target})",
        }
    except Exception as e:
        return {"error": f"Currency conversion failed: {str(e)}"}


WMO_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
}


def get_live_weather(city: str) -> dict:
    """Fetch real-time weather and forecast data for any city worldwide using the Open-Meteo public API.

    Args:
        city: The name of the city or destination (e.g. 'Tokyo', 'Paris', 'Kyoto', 'New York').

    Returns:
        A dictionary with real-time temperature, condition, humidity, and wind speed.
    """
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city.strip()}&count=1&language=en&format=json"
        geo_res = requests.get(geo_url, timeout=5).json()
        results = geo_res.get("results")
        if not results:
            return {"error": f"City '{city}' could not be located."}

        loc = results[0]
        lat, lon = loc["latitude"], loc["longitude"]

        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        )
        w_res = requests.get(weather_url, timeout=5).json()
        curr = w_res.get("current", {})
        code = curr.get("weather_code", 0)

        return {
            "city": loc["name"],
            "country": loc.get("country", ""),
            "timezone": loc.get("timezone", ""),
            "temperature_c": curr.get("temperature_2m"),
            "apparent_temperature_c": curr.get("apparent_temperature"),
            "humidity_percent": curr.get("relative_humidity_2m"),
            "wind_speed_kmh": curr.get("wind_speed_10m"),
            "condition": WMO_WEATHER_CODES.get(code, "Clear / Sunny"),
        }
    except Exception as e:
        return {"error": f"Failed to retrieve weather for '{city}': {str(e)}"}


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    query_lower = query.lower()
    if "sf" in query_lower or "san francisco" in query_lower:
        tz_identifier = "America/Los_Angeles"
    elif "tokyo" in query_lower or "kyoto" in query_lower:
        tz_identifier = "Asia/Tokyo"
    elif "paris" in query_lower:
        tz_identifier = "Europe/Paris"
    elif "taipei" in query_lower:
        tz_identifier = "Asia/Taipei"
    elif "new york" in query_lower or "ny" in query_lower:
        tz_identifier = "America/New_York"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


def get_maps_api_key() -> str:
    """Return the Google Maps API key from the local environment."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise ValueError("GOOGLE_MAPS_API_KEY environment variable is not set.")
    return key


def geocode_address(address: str) -> dict:
    """Turn a street address, landmark, or location name into geographic coordinates.

    Args:
        address: The address or landmark name to geocode (e.g. 'Tokyo Tower', 'Louvre Museum, Paris').

    Returns:
        A dictionary containing the name, formatted address, and location coordinates (latitude and longitude).
    """
    api_key = get_maps_api_key()
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": api_key}

    resp = requests.get(url, params=params, timeout=5)
    data = resp.json()
    if data.get("status") != "OK" or not data.get("results"):
        return {"error": f"Failed to geocode '{address}': {data.get('status', 'No results found')}"}

    top_result = data["results"][0]
    loc = top_result.get("geometry", {}).get("location", {})
    return {
        "name": address,
        "address": top_result.get("formatted_address", ""),
        "location": {
            "latitude": loc.get("lat"),
            "longitude": loc.get("lng"),
        },
    }


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "tourist_attraction",
    radius_meters: float = 1500.0,
) -> list[dict]:
    """Find nearby places (attractions, restaurants, cafes, museums) of a given type using Places API (New).

    Args:
        latitude: Latitude coordinate of the center point.
        longitude: Longitude coordinate of the center point.
        place_type: Place type to search for (e.g. 'tourist_attraction', 'restaurant', 'cafe', 'museum', 'park').
        radius_meters: Search radius in meters (default 1500m, max 50000m).

    Returns:
        A list of nearby places with their name, address, and location coordinates.
    """
    api_key = get_maps_api_key()
    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }

    body = {
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": float(latitude), "longitude": float(longitude)},
                "radius": float(radius_meters),
            }
        },
    }

    resp = requests.post(url, headers=headers, json=body, timeout=5)
    data = resp.json()
    places = data.get("places", [])

    results = []
    for p in places:
        results.append({
            "name": p.get("displayName", {}).get("text", ""),
            "address": p.get("formattedAddress", ""),
            "location": p.get("location", {}),
        })
    return results


async def generate_destination_image(
    prompt: str,
    tool_context: ToolContext = None,
) -> dict:
    """Generate a travel destination image or postcard using the gemini-3.1-flash-lite-image model.

    Saves the image bytes as an artifact for the Playground's Artifacts panel, uploads the same
    image bytes to the public Cloud Storage bucket, and returns its public HTTPS URL.

    Args:
        prompt: Detailed description of the travel scene, attraction, or postcard to generate.

    Returns:
        A dictionary containing the generated image's filename, public HTTPS URL, and prompt.
    """
    try:
        client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        image_bytes = None
        mime_type = "image/jpeg"
        for candidate in response.candidates:
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.inline_data and part.inline_data.data:
                        image_bytes = part.inline_data.data
                        mime_type = part.inline_data.mime_type or "image/jpeg"
                        break
            if image_bytes:
                break

        if not image_bytes:
            return {"error": "No image data was generated by the model."}

        extension = "jpg" if "jpeg" in mime_type or "jpg" in mime_type else "png"
        filename = f"destination_{uuid.uuid4().hex[:8]}.{extension}"

        # 1. Save artifact with tool_context.save_artifact for Playground Artifacts panel
        artifact_saved = False
        if tool_context:
            artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)
            artifact_saved = True

        # 2. Upload same bytes to public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(STORAGE_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{STORAGE_BUCKET_NAME}/{filename}"

        return {
            "prompt": prompt,
            "filename": filename,
            "public_url": public_url,
            "artifact_saved": artifact_saved,
            "message": f"Successfully generated destination image and uploaded to {public_url}",
        }
    except Exception as e:
        return {"error": f"Image generation failed: {str(e)}"}


async def generate_destination_video(
    prompt: str,
    tool_context: ToolContext = None,
) -> dict:
    """Generate a short cinematic preview video or scenery clip for a travel destination or attraction using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Saves the video bytes as an artifact using tool_context.save_artifact so it shows up in the Playground's Artifacts panel,
    uploads the same video bytes directly to the public Cloud Storage bucket, and returns its public HTTPS URL.

    Args:
        prompt: Detailed description of the travel scene, attraction, or landscape video to generate.

    Returns:
        A dictionary containing the generated video's filename, public HTTPS URL, and prompt.
    """
    try:
        client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
        interaction = client.interactions.create(
            model="gemini-omni-flash-preview",
            input=prompt,
        )

        video_bytes = None
        mime_type = "video/mp4"

        if hasattr(interaction, "output_video") and interaction.output_video:
            ov = interaction.output_video
            if hasattr(ov, "data") and ov.data:
                if isinstance(ov.data, (bytes, bytearray)):
                    video_bytes = bytes(ov.data)
                else:
                    video_bytes = base64.b64decode(ov.data)
            if hasattr(ov, "mime_type") and ov.mime_type:
                mime_type = ov.mime_type

        if not video_bytes:
            return {"error": "No video data was generated by the model."}

        filename = f"destination_video_{uuid.uuid4().hex[:8]}.mp4"

        # 1. Save artifact with tool_context.save_artifact for Playground Artifacts panel
        artifact_saved = False
        if tool_context:
            artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)
            artifact_saved = True

        # 2. Upload same video bytes directly to public Cloud Storage bucket
        bucket_name = "wanderlust-travel-media-qwiklabs-gcp-03-b19cec0b1d20"
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{bucket_name}/{filename}"

        return {
            "prompt": prompt,
            "filename": filename,
            "public_url": public_url,
            "artifact_saved": artifact_saved,
            "message": f"Successfully generated destination video and uploaded to {public_url}",
        }
    except Exception as e:
        return {"error": f"Video generation failed: {str(e)}"}


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

ROLE_DESCRIPTION = """You are WanderLust AI, an intelligent, inspiring, and meticulous travel concierge.
You help travelers discover attractions, explore destinations, build personalized day-by-day itineraries, and organize their trips.
You remember the traveler's stated preferences, all user allergies (food allergies, dietary restrictions, ingredient sensitivities, and medication/environmental allergies), favorite travel styles, past destinations, and budget constraints from previous conversations and use them to proactively protect and personalize recommendations for the traveler.

You have access to a real-time Firestore database containing curated attractions and saved itineraries:
- Use `search_attractions` to find attractions by city, category, or search keywords.
- Use `get_attraction_details` to look up in-depth details for a specific spot.
- Use `add_attraction` when the user asks to save or record a new attraction or favorite hidden gem into the catalog.
- Use `save_travel_itinerary` when the user wants to save their planned itinerary.
- Use `convert_currency` to convert amounts between currencies using real-time exchange rates for trip budgets.
- Use `get_live_weather` to fetch real-time destination weather, temperatures, and climate conditions worldwide.
- Use `geocode_address` to convert any landmark or destination name into latitude and longitude coordinates and an official address.
- Use `find_nearby_places` to find places of interest, restaurants, cafes, or attractions near given coordinates.
- Use `generate_destination_image` to generate scenic visual postcards or preview images of attractions and travel destinations.
- Use `generate_destination_video` to generate short cinematic preview videos or scenic clips of attractions, destinations, or travel landscapes using Gemini Omni.
- Use `get_current_time` to provide practical travel condition updates.
- You have Python code execution enabled via AgentEngineSandboxCodeExecutor: when mathematical calculations, budget breakdowns, itinerary duration computations, or data analysis are needed, write and execute Python code in your secure sandbox.

Always provide helpful, well-structured travel recommendations and suggestions tailored to the traveler's interests."""

UI_DESCRIPTION = (
    "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
    "Never nest a Card inside a Card. "
    "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
    "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
    "nothing in adk web). "
    "You may include one Image component, but only when you have a public https "
    "URL for the image (for example the URL an image tool returns after uploading "
    "to a public bucket). Set the Image url to that exact https link, for example "
    '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
    "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
    "not have a public URL, add a short Text line noting the image instead. "
    "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
    "headings and emphasis. "
    "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
    "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
)

instruction = schema_manager.generate_system_prompt(
    role_description=ROLE_DESCRIPTION,
    workflow_description="Analyze the request, call tools to gather facts or images, and return structured UI when appropriate.",
    ui_description=UI_DESCRIPTION,
    include_schema=True,
    include_examples=True,
)


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: after each turn, send the session to Memory Bank for extraction."""
    await callback_context.add_session_to_memory()
    return None


def memory_bank_service_builder():
    """Build a VertexAiMemoryBankService pointing to the Agent Engine for deployment."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


code_executor = AgentEngineSandboxCodeExecutor(
    sandbox_resource_name=SANDBOX_RESOURCE_NAME,
    agent_engine_resource_name=AGENT_ENGINE_RESOURCE_NAME,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        search_attractions,
        get_attraction_details,
        add_attraction,
        save_travel_itinerary,
        convert_currency,
        get_live_weather,
        geocode_address,
        find_nearby_places,
        generate_destination_image,
        generate_destination_video,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)

adk_app = agent_engines.AdkApp(
    app=app,
    memory_service_builder=memory_bank_service_builder,
)
