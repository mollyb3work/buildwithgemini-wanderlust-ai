#!/usr/bin/env python3
"""Seed Firestore with curated travel attractions for WanderLust AI."""

from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-03-b19cec0b1d20"

SEED_ATTRACTIONS = [
    {
        "id": "tokyo_shibuya_sky",
        "name": "Shibuya Sky",
        "city": "Tokyo",
        "country": "Japan",
        "category": "Sightseeing",
        "rating": 4.8,
        "price_level": "$$",
        "estimated_duration_hours": 2.0,
        "description": "A 360-degree open-air observation deck sitting 229 meters above Shibuya Crossing, offering breathtaking views of Tokyo and Mount Fuji.",
        "tags": ["observation deck", "night view", "photography", "modern"]
    },
    {
        "id": "tokyo_sensoji",
        "name": "Sensō-ji Temple",
        "city": "Tokyo",
        "country": "Japan",
        "category": "Culture",
        "rating": 4.7,
        "price_level": "Free",
        "estimated_duration_hours": 2.5,
        "description": "Tokyo's oldest and most famous Buddhist temple in Asakusa, featuring the iconic Kaminarimon gate and Nakamise shopping street.",
        "tags": ["temple", "historic", "culture", "street food"]
    },
    {
        "id": "kyoto_fushimi_inari",
        "name": "Fushimi Inari Taisha",
        "city": "Kyoto",
        "country": "Japan",
        "category": "Culture & Nature",
        "rating": 4.9,
        "price_level": "Free",
        "estimated_duration_hours": 3.0,
        "description": "Iconic Shinto shrine renowned for its mesmerizing path of thousands of vibrant vermilion torii gates winding up Mount Inari.",
        "tags": ["shrine", "hiking", "photography", "historic"]
    },
    {
        "id": "paris_louvre",
        "name": "Louvre Museum",
        "city": "Paris",
        "country": "France",
        "category": "Museum & Art",
        "rating": 4.8,
        "price_level": "$$$",
        "estimated_duration_hours": 4.0,
        "description": "The world's largest art museum and historic monument in Paris, home to the Mona Lisa and the Venus de Milo.",
        "tags": ["art", "museum", "historic", "renaissance"]
    },
    {
        "id": "paris_eiffel_tower",
        "name": "Eiffel Tower",
        "city": "Paris",
        "country": "France",
        "category": "Landmark",
        "rating": 4.7,
        "price_level": "$$",
        "estimated_duration_hours": 2.5,
        "description": "The quintessential wrought-iron lattice tower on the Champ de Mars, offering panoramic vistas of Paris.",
        "tags": ["landmark", "romance", "night view", "architecture"]
    },
    {
        "id": "ny_central_park",
        "name": "Central Park",
        "city": "New York",
        "country": "USA",
        "category": "Nature & Parks",
        "rating": 4.8,
        "price_level": "Free",
        "estimated_duration_hours": 3.0,
        "description": "An iconic 843-acre urban oasis in Manhattan featuring walking paths, bridges, lakes, and open meadows.",
        "tags": ["park", "nature", "walk", "picnic"]
    },
    {
        "id": "taipei_101",
        "name": "Taipei 101 Observatory",
        "city": "Taipei",
        "country": "Taiwan",
        "category": "Sightseeing",
        "rating": 4.7,
        "price_level": "$$",
        "estimated_duration_hours": 2.0,
        "description": "A world-famous skyscraper shaped like a bamboo stalk, boasting high-speed elevators and an 89th-floor observatory over Taipei Basin.",
        "tags": ["skyscraper", "observation deck", "architecture", "city view"]
    },
    {
        "id": "taipei_jiufen",
        "name": "Jiufen Old Street",
        "city": "New Taipei",
        "country": "Taiwan",
        "category": "Culture & Food",
        "rating": 4.6,
        "price_level": "$",
        "estimated_duration_hours": 3.5,
        "description": "A picturesque mountain town with narrow cobbled alleyways lined with traditional teahouses, street food stalls, and red lanterns.",
        "tags": ["old street", "teahouse", "street food", "mountain view"]
    }
]


def seed():
    print(f"Connecting to Firestore for project: {PROJECT_ID}...")
    db = firestore.Client(project=PROJECT_ID)
    collection = db.collection("attractions")
    
    print(f"Seeding {len(SEED_ATTRACTIONS)} attractions...")
    for item in SEED_ATTRACTIONS:
        doc_ref = collection.document(item["id"])
        doc_ref.set(item)
        print(f"  ✓ Seeded: {item['name']} ({item['city']}, {item['country']})")
    
    print("Seeding completed successfully!")


if __name__ == "__main__":
    seed()
