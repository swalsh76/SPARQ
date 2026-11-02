"""The prompts sent to the model node (PLAN §6.5, N4): committed, and part of every cache key."""

READ_WEATHER = "\n".join(
    [
        "You read the rules text of a prediction market about weather. Reply with one JSON object",
        "and nothing else, with these keys:",
        '"site": the climate site code or weather station identifier the text names (for example',
        '"CLINYC" or "KNYC"), or null if the text names none;',
        '"direction": "greater" if the market is about more than a value, "less" if about less',
        'than a value, "between" if about a range;',
        '"lower": the lower number of the range, or the value for "greater", otherwise null;',
        '"upper": the upper number of the range, or the value for "less", otherwise null.',
        "Numbers only, no units.",
        "",
        "Rules text:",
        "{rules}",
        "",
    ]
)
