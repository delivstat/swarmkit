# Sample Run: Hanuman Sanjeevani Story (English)

## Input
```
Tell me the story of Hanuman carrying the mountain. My son loves
Hanuman and I want to tell him this story tonight.
```

## Model
Kimi K2.6 via OpenRouter

## Tool Calls (6 turns, 71.6s)

| Turn | Tool | Result |
|---|---|---|
| 1 | `brain-search` (Hanuman mountain) | 175B — found `hanuman-sanjeevani` knowledge block |
| 2 | `search-scripture` (Hanuman Sanjeevani) | 4KB from Ramayana ChromaDB |
| 3 | `search-scripture` (Lakshmana wounded) | 3KB |
| 4 | `search-scripture` (herb mountain peak) | 3KB |
| 5 | `search-scripture` (Yuddha Kanda Sanjeevani) | 5KB |
| 6 | `get-verse` (ramayana:yuddha:74:31, 102:27, 102:28) detail=quote | 141B + 132B + 137B |

## GBrain Match
`hanuman-sanjeevani` knowledge block — matched on first search.

## Notes
- GBrain had a PGLite lock timeout (another run was using it simultaneously)
- Fell back to ChromaDB Ramayana collection for verse details
- Pulled actual Sanskrit shlokas from Yuddha Kanda (6.74.31, 6.102.27)
- Adapted the full story for a child, ended with "Jai Hanuman! 🙏"

## Response

**The Night Hanuman Brought the Mountain**

The great war was raging in Lanka. Rama's army of monkeys and bears was fighting bravely against the demon king Ravana. But Ravana's son Indrajit struck Lakshmana with a powerful weapon. Lakshmana fell to the ground, pale and unmoving.

The physician Sushena said, "Before the sun rises, he must be given the *Sanjeevani* herb — a magical plant that grows far away, on a glowing mountain in the Himalayas."

Hanuman flew north toward the great mountains. The sages describe:

> *तयोः शिखरयोर्मध्ये प्रदीप्तमतुलप्रभम् ।*
> *सर्वौषधियुतं वीर द्रक्ष्यस्यौषधिपर्वतम् ॥*
>
> "Between those two peaks, O hero, you will see a mountain glowing with unmatched splendor, covered with all kinds of medicinal herbs." *(Ramayana 6.74.31)*

Hanuman couldn't tell which herb was Sanjeevani among thousands. So:

> *तस्य बुद्धिः समुत्पन्ना मारुतेरमितौजसः ।*
> *इदमेव गमिष्यामि गृहीत्वा शिखरं गिरेः ॥*
>
> "Then the thought arose in Maruti's mind — 'I will simply take the peak of this mountain itself!'" *(Ramayana 6.102.27)*

He uprooted the entire mountain peak and flew back. Lakshmana was saved.

**For your son:** When Hanuman couldn't find the one small herb, he didn't say "I failed" — he just brought the whole mountain! That's the kind of courage and cleverness Hanuman had. Sleep well, little one. Jai Hanuman! 🙏
