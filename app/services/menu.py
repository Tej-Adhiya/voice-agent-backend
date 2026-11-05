import os
import re
from typing import Optional, List, Dict, Any

# Allergen configuration matching the top of the menu file
ALLERGEN_MAP = {
    1: "Gluten",
    2: "Crustaceans",
    3: "Eggs",
    4: "Fish",
    5: "Peanuts",
    6: "Soy",
    7: "Milk",
    8: "Nuts",
    9: "Celery",
    10: "Mustard",
    11: "Sesame",
    12: "Sulphur",
    13: "Lupin",
    14: "Molluscs"
}

# Map common allergen/dietary keywords to specific allergen ID lists to exclude
ALLERGEN_NAME_TO_IDS = {
    "gluten": [1],
    "crustacean": [2],
    "crustaceans": [2],
    "shellfish": [2, 14],
    "seafood": [2, 4, 14],
    "egg": [3],
    "eggs": [3],
    "fish": [4],
    "peanut": [5],
    "peanuts": [5],
    "soy": [6],
    "soya": [6],
    "milk": [7],
    "dairy": [7],
    "lactose": [7],
    "cheese": [7],
    "butter": [7],
    "cream": [7],
    "nut": [8],
    "nuts": [8],
    "hazelnut": [8],
    "hazelnuts": [8],
    "pinenut": [8],
    "pinenuts": [8],
    "almond": [8],
    "almonds": [8],
    "pistachio": [8],
    "pistachios": [8],
    "celery": [9],
    "mustard": [10],
    "sesame": [11],
    "sulphur": [12],
    "sulfur": [12],
    "lupin": [13],
    "mollusc": [14],
    "molluscs": [14],
    "clams": [14],
    "oyster": [14],
    "oysters": [14]
}

CATEGORIES = [
    "SALAD & STARTER",
    "SEAFOOD & CARPACCIO BAR",
    "HOT STARTER",
    "PASTA",
    "FISH & MEAT",
    "SIDES €7-12",
    "DESSERTS (to share)",
    "SOFTS",
    "HOT",
    "COCKTAILS (0.0% non-alcoholic) €13-14",
    "BEER",
    "SIGNATURE COCKTAILS",
    "SPIRITS",
    "SPARKLING (glass/bottle)",
    "WHITE (glass/bottle)",
    "ROSÉ (glass/bottle)",
    "RED (glass/bottle)"
]

CATEGORY_MAPPING = {
    "starters": ["SALAD & STARTER", "SEAFOOD & CARPACCIO BAR", "HOT STARTER"],
    "pasta": ["PASTA"],
    "mains": ["FISH & MEAT"],
    "sides": ["SIDES €7-12"],
    "desserts": ["DESSERTS (to share)"],
    "drinks": ["SOFTS", "HOT", "COCKTAILS (0.0% non-alcoholic) €13-14", "BEER", "SIGNATURE COCKTAILS", "SPIRITS"],
    "wine": ["SPARKLING (glass/bottle)", "WHITE (glass/bottle)", "ROSÉ (glass/bottle)", "RED (glass/bottle)"],
    "food": ["SALAD & STARTER", "SEAFOOD & CARPACCIO BAR", "HOT STARTER", "PASTA", "FISH & MEAT", "DESSERTS (to share)"]
}


class MenuService:
    def __init__(self, menu_filepath: Optional[str] = None):
        if menu_filepath:
            self.menu_filepath = menu_filepath
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.menu_filepath = os.environ.get("MENU_FILEPATH", os.path.join(base_dir, "sample_menu"))
        
        self._cached_items = []
        self._last_mtime = 0.0

    def parse_menu(self) -> List[Dict[str, Any]]:
        """
        Parses the menu text file dynamically and returns a list of items with structure:
        {
          "name": str,
          "price": str,
          "allergens": List[int],
          "description": str,
          "category": str
        }
        Caches the results to avoid disk reads on every call unless the file is updated.
        """
        if not os.path.exists(self.menu_filepath):
            return []

        try:
            current_mtime = os.path.getmtime(self.menu_filepath)
            if current_mtime == self._last_mtime and self._cached_items:
                return self._cached_items
        except Exception:
            current_mtime = 0.0

        with open(self.menu_filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        items = []
        current_category = None

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Skip section headers like == FLYINGTRAY ... ==
            if line_str.startswith("==") and line_str.endswith("=="):
                continue

            # Check if it's a category header
            if line_str in CATEGORIES:
                current_category = line_str
                continue

            # Skip the main title/allergen legend at the top
            if "RESTAURANT MENU" in line_str or line_str.startswith("Allergens:"):
                continue

            # Parse lines (may contain multiple items separated by " | ")
            # E.g. "Potato Gratin €8 [7] | Fries €7 [7] | Truffle Fries w/Parmesan €12 [7]"
            parts = [p.strip() for p in line_str.split("|")]
            for part in parts:
                if not part:
                    continue
                
                item = self._parse_single_item(part, current_category)
                if item:
                    items.append(item)

        self._cached_items = items
        self._last_mtime = current_mtime
        return items

    def _parse_single_item(self, text: str, category: Optional[str]) -> Optional[Dict[str, Any]]:
        # Regex to match price e.g., €19, €7/each, €80/€280, €92 for 2, €13/€63
        price_regex = r'(€\s*\d+(?:\s*[\/\-]\s*€?\d+)?(?:\s+for\s+\d+(?:-\d+)?)?(?:\s*\/each)?)'
        price_match = re.search(price_regex, text)
        if not price_match:
            # If no price, it might not be a menu item line
            return None

        price = price_match.group(0).strip()
        
        # Extract allergen IDs in brackets e.g. [1,7,12]
        allergen_match = re.search(r'\[([\d,]+)\]', text)
        allergens = []
        if allergen_match:
            allergens = [int(x) for x in allergen_match.group(1).split(',')]
            # Remove brackets and numbers from text
            text = text.replace(allergen_match.group(0), "")

        # Remove price from text
        text = text.replace(price_match.group(0), "")

        # Handle GIN line prefix prefixing the first item in the split list
        # "GIN (served w/Fever Tree +€5): Gin Mare" -> name should be "Gin Mare"
        # We can clean prefixes ending in ":"
        if ":" in text and not text.startswith("http"):
            text_parts = text.split(":", 1)
            # If the part after colon contains letters, let's use it
            if len(text_parts) > 1 and len(text_parts[1].strip()) > 2:
                text = text_parts[1]

        # Split remaining text by " - " into name and description
        if " - " in text:
            name_part, desc_part = text.split(" - ", 1)
        else:
            name_part, desc_part = text, ""

        name = name_part.strip()
        description = desc_part.strip()

        # Clean multiple whitespaces
        name = re.sub(r'\s+', ' ', name)
        description = re.sub(r'\s+', ' ', description)

        return {
            "name": name,
            "price": price,
            "allergens": allergens,
            "description": description,
            "category": category or "UNcategorized"
        }

    def get_recommendations(
        self,
        category: Optional[str] = None,
        exclude_allergens: Optional[List[str]] = None,
        search_query: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Filters the parsed menu items and returns recommendations.
        """
        all_items = self.parse_menu()

        # 1. Normalize exclusions
        excluded_ids = set()
        if exclude_allergens:
            for item in exclude_allergens:
                item_lower = item.strip().lower()
                if item_lower.isdigit():
                    excluded_ids.add(int(item_lower))
                elif item_lower in ALLERGEN_NAME_TO_IDS:
                    excluded_ids.update(ALLERGEN_NAME_TO_IDS[item_lower])

        # 2. Filter items
        filtered = []
        target_categories = None
        if category:
            cat_lower = category.strip().lower()
            if cat_lower in CATEGORY_MAPPING:
                target_categories = CATEGORY_MAPPING[cat_lower]

        for item in all_items:
            # Category filter
            if target_categories and item["category"] not in target_categories:
                continue

            # Allergen filter
            item_allergens = set(item["allergens"])
            if excluded_ids & item_allergens:
                continue

            # Search query filter (matches name, description or category name)
            if search_query:
                q = search_query.strip().lower()
                # Handle general "vegan" or "vegetarian" requests by checking names/descriptions
                in_name = q in item["name"].lower()
                in_desc = q in item["description"].lower()
                in_cat = q in item["category"].lower()
                if not (in_name or in_desc or in_cat):
                    continue

            filtered.append(item)

        # Build spoken summary preview (first 3 items)
        preview_items = filtered[:3]
        if not filtered:
            if excluded_ids:
                spoken = "I'm sorry, I couldn't find any items matching those preferences that are free of those allergens."
            else:
                spoken = "I'm sorry, I couldn't find any recommendations matching that request."
        else:
            item_descriptions = []
            for item in preview_items:
                desc = f"{item['name']} for {item['price']}"
                if item["description"]:
                    desc += f", which is {item['description']}"
                item_descriptions.append(desc)
            
            if len(item_descriptions) == 1:
                spoken = f"We have {item_descriptions[0]}."
            elif len(item_descriptions) == 2:
                spoken = f"We have {item_descriptions[0]}, or {item_descriptions[1]}."
            else:
                spoken = f"We have options like {', '.join(item_descriptions[:-1])}, or {item_descriptions[-1]}."
            
            if len(filtered) > 3:
                spoken += f" And we have {len(filtered) - 3} other items in this section."

        # Map allergen IDs back to friendly names in response items
        response_items = []
        for ri in filtered:
            response_items.append({
                "name": ri["name"],
                "price": ri["price"],
                "description": ri["description"],
                "category": ri["category"],
                "allergens": [ALLERGEN_MAP[a] for a in ri["allergens"] if a in ALLERGEN_MAP]
            })

        return {
            "recommendations": response_items,
            "spoken_summary": spoken,
            "total_items": len(response_items),
            "excluded_allergens": [ALLERGEN_MAP[a] for a in excluded_ids if a in ALLERGEN_MAP]
        }
