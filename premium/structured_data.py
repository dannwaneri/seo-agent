import json


def extract_json_ld(json_ld_blocks: list[str]) -> dict:
    if not json_ld_blocks:
        return {"blocks": [], "status": "MISSING", "flags": ["No structured data found"]}

    parsed_blocks = []
    flags = []
    has_fail = False
    has_warn = False

    for i, raw in enumerate(json_ld_blocks, start=1):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Strip the error message to the first sentence (before newlines)
            msg = str(exc).split("\n")[0]
            flags.append(f"Block {i}: invalid JSON — {msg}")
            has_fail = True
            continue

        if "@context" not in data:
            flags.append(f"Block {i}: missing @context field")
            has_fail = True
            continue

        if "@type" not in data:
            flags.append(f"Block {i}: missing @type field")
            has_warn = True
            parsed_blocks.append(data)
            continue

        parsed_blocks.append(data)

    if has_fail:
        status = "FAIL"
    elif has_warn:
        status = "WARN"
    else:
        status = "PASS"

    return {"blocks": parsed_blocks, "status": status, "flags": flags}
