# verify_darija_labels.py
# Run this once to confirm what LABEL_0 / LABEL_1 actually mean for
# BenhamdaneNawfal/sentiment-analysis-darija. sentiment_analyzer.py
# currently ASSUMES LABEL_0=negative, LABEL_1=positive (logged as an
# UNVERIFIED assumption every time it's used) — this script tells you
# definitively whether that assumption is correct.
#
# Usage: python verify_darija_labels.py

from transformers import pipeline

model = pipeline("text-classification", model="BenhamdaneNawfal/sentiment-analysis-darija")

# Obviously positive and obviously negative Darija sentences.
POSITIVE_EXAMPLES = [
    "هاد اللاعب زوين بزاف، فرحتني اليوم",       # "this player is great, made me happy today"
    "لعب مزيان بزاف اليوم، برافو عليه",           # "he played very well today, well done"
]
NEGATIVE_EXAMPLES = [
    "هاد اللاعب خايب بزاف، خيب أملي",             # "this player is terrible, disappointed me"
    "ماعجبنيش الأداء ديالو اليوم، ضعيف بزاف",     # "I didn't like his performance today, very weak"
]

print("POSITIVE examples (should mostly return the same label):")
for text in POSITIVE_EXAMPLES:
    result = model(text)[0]
    print(f"  {text!r} -> {result}")

print("\nNEGATIVE examples (should mostly return the OTHER label):")
for text in NEGATIVE_EXAMPLES:
    result = model(text)[0]
    print(f"  {text!r} -> {result}")

print("\n" + "=" * 60)
print("If positive examples mostly -> LABEL_1 and negative -> LABEL_0,")
print("the current code (LABEL_0=negative, LABEL_1=positive) is CORRECT.")
print("If it's the other way around, flip the assumption in")
print("analytics/nlp/sentiment_analyzer.py's normalize_label() function.")
print("=" * 60)
