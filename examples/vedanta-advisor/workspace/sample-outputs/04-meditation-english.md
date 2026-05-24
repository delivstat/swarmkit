# Sample Run: Meditation (English)

## Input
```
I try to meditate but my mind won't stop racing. I sit down and within
seconds I'm thinking about work, my to-do list, arguments I had.
How do I quiet my mind?
```

## Model
Kimi K2.6 via OpenRouter

## Tool Calls (7 turns, 69s)

| Turn | Tool | Result |
|---|---|---|
| 1 | `brain-search` (meditation, mind) | 2KB + 2KB — found `meditation-and-mind` wisdom block |
| 2 | `get-verse` (gita:6:34, gita:6:35, gita:6:5) detail=quote | 177B + 124B + 170B |
| 3 | `get-verse` (gita:6:6, gita:2:14) detail=quote | 166B + 223B |
| 4 | `search-scripture` (dhyana yoga) | 2KB |
| 5 | `search-scripture` + `brain-search` | 1KB + 33B |
| 6 | `get-verse` (gita:6:26, gita:6:19) + `brain-search` | 219B + 166B + 33B |
| 7 | `get-verse` (gita:6:34, gita:6:35) detail=full | 11KB + 14KB (for commentary comparison) |

## GBrain Match
`meditation-and-mind` wisdom block — matched on first search (2KB).

## Response

You're not broken, and you're not doing it wrong. What you're experiencing is *exactly* what Arjuna felt on the battlefield when Krishna taught him about meditation.

Arjuna looked at Krishna and said what every meditator thinks:

> **चञ्चलं हि मनः कृष्ण प्रमाथि बलवद् दृढम् |**
> **तस्याहं निग्रहं मन्ये वायोरिव सुदुष्करम् ||६-३४||**
>
> *"The mind is restless, turbulent, strong and unyielding, O Krishna. I think controlling it is as difficult as controlling the wind."* (Gita 6.34)

Krishna's answer:

> **असंशयं महाबाहो मनो दुर्निग्रहं चलम् |**
> **अभ्यासेन तु कौन्तेय वैराग्येण च गृह्यते ||६-३५||**
>
> *"Undoubtedly the mind is difficult to control and restless. But it is restrained by* **abhyasa** *(practice) and* **vairagya** *(dispassion)."* (Gita 6.35)

Two tools: patient practice and letting go. The wandering isn't failure; noticing the wandering IS the practice.
