from collections import defaultdict

def parse_edifact(file_path: str) -> dict:
    """
    Parses an EDIFACT .edi file into a structured dictionary:
    - Single-occurrence tags → flat list
    - Repeated tags → list of dicts keyed by qualifier
    - Composite elements split by ':'
    """
    segments = defaultdict(list)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    for segment in content.split("'"):
        segment = segment.strip()
        if not segment:
            continue

        parts = [part.strip() for part in segment.split("+")]
        tag = parts[0]

        # Special handling for qualifier-based segments (DTM, NAD, LIN, PRI, MOA, etc.)
        if tag in {"DTM", "NAD", "LIN", "PRI", "MOA"}:
            qualifier = parts[1]
            values = []

            if ":" in qualifier:
                qualifier_parts = qualifier.split(":")
                qualifier = qualifier_parts[0]
                for item in qualifier_parts[1:]:
                    if item:
                        values.append(item)

            for part in parts[2:]:
                if ":" in part:
                    values.extend(part.split(":"))
                elif part:
                    values.append(part)
            segments[tag].append({qualifier: values})
        else:
            # Flatten composite elements for other tags
            flat_parts = []
            for part in parts[1:]:
                if ":" in part:
                    flat_parts.extend(part.split(":"))
                elif part:
                    flat_parts.append(part)
            segments[tag].append(flat_parts)

    # Post-process: unwrap single-occurrence tags
    result = {}
    for tag, values in segments.items():
        if len(values) == 1 and tag not in {"DTM", "NAD", "LIN", "PRI", "MOA"}:
            result[tag] = values[0]
        else:
            result[tag] = values

    return result