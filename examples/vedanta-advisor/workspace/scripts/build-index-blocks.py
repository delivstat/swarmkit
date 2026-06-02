# /// script
# requires-python = ">=3.11"
# dependencies = ["chromadb>=0.5", "httpx>=0.27"]
# ///
# ruff: noqa: E501, E402, PLR0915, PLR0912, PLC0415, F841
"""Build character, concept, and text-overview blocks for GBrain.

These blocks fill the gap between wisdom blocks (life themes) and
knowledge blocks (specific stories). They give the advisor anchors
for STUDY mode queries about characters, philosophical concepts,
and specific texts.

Usage:
    cd /home/srijith/dev/vedanta-advisor
    uv run workspace/scripts/build-index-blocks.py [--dry-run] [--type character|concept|text|all]

Env:
    OPENROUTER_API_KEY — required for LLM calls
    VEDANTA_CHROMADB_DIR — ChromaDB path (default: ./knowledge/chromadb)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from functools import partial
from pathlib import Path

print = partial(print, flush=True)

import httpx

try:
    import chromadb
except ImportError:
    print("Install chromadb: pip install chromadb", file=sys.stderr)
    sys.exit(1)


@dataclass
class BlockSpec:
    slug: str
    block_type: str
    title: str
    description: str
    search_queries: list[str]
    verse_refs: list[str]


# ---- CHARACTER BLOCKS ----

CHARACTER_BLOCKS = [
    BlockSpec(
        slug="character/indra",
        block_type="character",
        title="Indra — King of the Devas",
        description="Indra: king of devas, lord of thunder and rain. Deeply flawed — lustful (Ahalya), insecure (fears ascetics), jealous (persecutes devotees). His position is functional (cosmic order) not moral. Key stories: Ahalya curse, Vritra killing, Nahusha replacement, Govardhan. Epithet Sahasraksha from punishment.",
        search_queries=["Indra king devas ego jealousy", "Indra Ahalya curse Gautama", "Nahusha becomes king gods Indra throne", "Indra Vritra Brahminicide"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/arjuna",
        block_type="character",
        title="Arjuna — The Conflicted Warrior",
        description="Arjuna: third Pandava, greatest archer, Krishna's friend and student. His crisis on the battlefield — unwilling to fight family — triggers the entire Bhagavad Gita. Represents every person facing moral paralysis. Key moments: Gita dialogue, Pashupatastra, Subhadra marriage, Abhimanyu's death.",
        search_queries=["Arjuna warrior crisis battlefield Gita", "Arjuna refuses fight family Kurukshetra", "Arjuna Pashupatastra Shiva"],
        verse_refs=["gita:1:28", "gita:2:7", "gita:11:9"],
    ),
    BlockSpec(
        slug="character/krishna",
        block_type="character",
        title="Krishna — Teacher, Friend, Divine",
        description="Krishna: cowherd, prince, diplomat, charioteer, guru, divine incarnation. Multifaceted — playful in Vrindavan, strategic in Kurukshetra, compassionate in the Gita. Teaches through relationship, not commandment. Key roles: Gita teacher, Mahabharata diplomat, Govardhan protector, Draupadi's protector.",
        search_queries=["Krishna teacher charioteer Gita divine", "Krishna Vrindavan childhood", "Krishna Draupadi protection", "Krishna universal form Vishwarupa"],
        verse_refs=["gita:4:7", "gita:4:8", "gita:11:32"],
    ),
    BlockSpec(
        slug="character/draupadi",
        block_type="character",
        title="Draupadi — Fire-Born Queen",
        description="Draupadi: born from sacrificial fire, wife of five Pandavas, queen humiliated in the assembly hall. Her disrobing is the moral turning point of the Mahabharata. Fierce, intelligent, outspoken. Challenges patriarchal silence. Key moments: svayamvara, disrobing, exile, vengeance demand.",
        search_queries=["Draupadi disrobing assembly hall humiliation", "Draupadi fire born five husbands", "Draupadi exile forest dignity"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/rama",
        block_type="character",
        title="Rama — The Ideal King",
        description="Rama: prince of Ayodhya, Vishnu avatar, the maryada purushottama (ideal man). Exiled 14 years, loses Sita to Ravana, builds army of monkeys, wins war. But also the complex figure who abandons pregnant Sita for public opinion. The tradition debates this endlessly.",
        search_queries=["Rama ideal king exile Ayodhya", "Rama Sita Lanka war", "Rama abandons Sita Uttara Kanda", "Rama maryada purushottama"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/sita",
        block_type="character",
        title="Sita — Strength Through Suffering",
        description="Sita: daughter of the earth, Rama's wife, abducted by Ravana. Her captivity in Lanka — refusing Ravana, maintaining dignity — is a study in inner strength. The agni pariksha (fire ordeal) and eventual return to the earth are among the most debated episodes in Hindu literature.",
        search_queries=["Sita abducted Ravana Lanka captivity", "Sita fire ordeal agni pariksha", "Sita earth swallows Uttara Kanda", "Sita Ashoka grove dignity"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/karna",
        block_type="character",
        title="Karna — The Tragic Hero",
        description="Karna: abandoned son of Kunti and Surya, raised by a charioteer. Greatest warrior on the wrong side. His tragedy: loyalty to Duryodhana who gave him dignity when everyone else rejected him. Generous to a fault — gives away his armor. Killed unfairly by Arjuna. The most sympathetic 'villain' in world literature.",
        search_queries=["Karna tragic hero charioteer son Kunti", "Karna Duryodhana loyalty friendship", "Karna armor kavach kundal generous", "Karna death Arjuna unfair"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/yudhishthira",
        block_type="character",
        title="Yudhishthira — The Dharma King",
        description="Yudhishthira: eldest Pandava, son of Dharma himself. Never lies — except once, and it costs him. Gambles away his kingdom, brothers, wife. Bears humiliation rather than abandon dharma. His dog at heaven's gate is one of literature's greatest moral tests.",
        search_queries=["Yudhishthira dharma truth never lies", "Yudhishthira dice game loss kingdom", "Yudhishthira dog heaven gate test"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/bhishma",
        block_type="character",
        title="Bhishma — The Terrible Vow",
        description="Bhishma: gave up his throne, his right to marry, his entire life — for his father's happiness. His vow of celibacy is so extreme that even the gods respect it. But his tragedy: bound by that vow, he stands silent while Draupadi is humiliated. Duty can become a prison.",
        search_queries=["Bhishma vow celibacy throne father", "Bhishma bed arrows death", "Bhishma silent Draupadi disrobing"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/hanuman",
        block_type="character",
        title="Hanuman — Devotion as Superpower",
        description="Hanuman: monkey god, son of Vayu, Rama's greatest devotee. His strength is literally proportional to his devotion. Leaps across the ocean, carries a mountain, burns Lanka. Yet the most powerful being in the Ramayana is also the most humble. Opens his chest to show Rama in his heart.",
        search_queries=["Hanuman devotion Rama strength monkey", "Hanuman leaps ocean Lanka mountain", "Hanuman burns Lanka fire tail", "Hanuman opens chest Rama heart"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/ravana",
        block_type="character",
        title="Ravana — The Learned Demon",
        description="Ravana: ten-headed king of Lanka, great scholar, Shiva devotee, veena player. Not a simple villain — he's the most learned being of his age. His downfall is desire (for Sita) and ego (refusing to return her). The tradition respects his scholarship while condemning his actions.",
        search_queries=["Ravana ten heads Lanka scholar Shiva", "Ravana Sita abduction ego desire", "Ravana veena devotee Shiva learned"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/duryodhana",
        block_type="character",
        title="Duryodhana — Jealousy Incarnate",
        description="Duryodhana: eldest Kaurava, consumed by jealousy of the Pandavas from childhood. Not unintelligent — he's a skilled ruler and a generous friend to Karna. But envy poisons everything. The dice game, the disrobing, the war — all spring from his inability to accept others' success.",
        search_queries=["Duryodhana jealousy Pandavas hatred", "Duryodhana dice game cheat", "Duryodhana Karna friendship generous", "Duryodhana thigh Draupadi insult"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/vidura",
        block_type="character",
        title="Vidura — The Voice of Conscience",
        description="Vidura: half-brother to Dhritarashtra, born of a servant. The wisest counselor in Hastinapura, repeatedly warning Dhritarashtra against his sons' adharma. Always ignored. His niti (practical wisdom) is among the most quotable teachings in the Mahabharata.",
        search_queries=["Vidura wisdom counsel Dhritarashtra warning", "Vidura niti practical wisdom", "Vidura advises king servant son"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/shakuntala",
        block_type="character",
        title="Shakuntala — Love, Loss, and Identity",
        description="Shakuntala: raised in a forest hermitage, falls in love with King Dushyanta. He forgets her due to a sage's curse. She must prove her own identity to the man who loved her. Her story — told by Kalidasa and in the Mahabharata — is about being forgotten by those who should remember you.",
        search_queries=["Shakuntala Dushyanta love forgotten curse", "Shakuntala ring recognition Kalidasa"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/shiva",
        block_type="character",
        title="Shiva — The Destroyer Who Renews",
        description="Shiva: ascetic and householder, destroyer and protector, meditator and dancer. Lives on Mount Kailasa covered in ash, wearing snakes, the moon in his hair. Drinks poison to save the world (Neelkantha). His tandava dance dissolves and recreates the universe. The god of paradox.",
        search_queries=["Shiva destroyer renewer ascetic householder", "Shiva Neelkantha poison ocean churning", "Shiva tandava dance destruction", "Shiva Parvati Kailasa meditation"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="character/vishnu",
        block_type="character",
        title="Vishnu — The Preserver",
        description="Vishnu: the sustainer of cosmic order, who incarnates whenever dharma declines. His dashavatara (ten incarnations) span from fish to future warrior. As Rama he models duty, as Krishna he teaches freedom. Reclining on Shesha in the cosmic ocean, dreaming the universe into being.",
        search_queries=["Vishnu preserver incarnation dashavatara", "Vishnu Rama Krishna avatar dharma", "Vishnu Shesha cosmic ocean Vaikuntha"],
        verse_refs=["gita:4:7", "gita:4:8"],
    ),
]


# ---- CONCEPT BLOCKS ----

CONCEPT_BLOCKS = [
    BlockSpec(
        slug="concept/dharma",
        block_type="concept",
        title="Dharma — Duty, Order, Righteousness",
        description="Dharma: the most untranslatable word in Hindu philosophy. Cosmic order, personal duty, moral law, righteousness — all at once. Contextual: a warrior's dharma differs from a teacher's. The Mahabharata's central question: what IS dharma when duties conflict?",
        search_queries=["dharma duty righteousness cosmic order", "dharma conflict Mahabharata", "svadharma own duty Gita"],
        verse_refs=["gita:3:35", "gita:18:47"],
    ),
    BlockSpec(
        slug="concept/karma",
        block_type="concept",
        title="Karma — Action and Consequence",
        description="Karma: not fate, not punishment — consequence. Every action creates results. The Gita's revolution: act without attachment to results (nishkama karma). Karma is not 'what happens to you' but 'what you set in motion.' Three types: sanchita (accumulated), prarabdha (current), kriyamana (being created now).",
        search_queries=["karma action consequence nishkama", "karma three types sanchita prarabdha", "karma yoga detached action Gita"],
        verse_refs=["gita:2:47", "gita:3:5", "gita:4:18"],
    ),
    BlockSpec(
        slug="concept/moksha",
        block_type="concept",
        title="Moksha — Liberation",
        description="Moksha: freedom from the cycle of birth and death (samsara). The ultimate goal in Hindu philosophy. Three paths: jnana (knowledge), bhakti (devotion), karma (action). Different darshanas disagree on what moksha IS: merger with Brahman (Advaita), eternal presence with God (Vishishtadvaita), or distinct liberation (Dvaita).",
        search_queries=["moksha liberation samsara freedom cycle", "moksha jnana bhakti karma paths", "moksha Advaita Vishishtadvaita Dvaita differences"],
        verse_refs=["gita:4:35", "gita:5:28", "gita:18:66"],
    ),
    BlockSpec(
        slug="concept/maya",
        block_type="concept",
        title="Maya — Illusion and the Nature of Reality",
        description="Maya: the power that makes the unreal appear real. Not 'the world is fake' — rather, the world is real but not ultimate. Shankara: maya is neither real nor unreal (anirvachaniya). The snake-rope analogy: you see a snake (world), but it's a rope (Brahman). Fear is real until you see the truth.",
        search_queries=["maya illusion reality Shankara", "maya snake rope analogy Brahman", "maya anirvachaniya neither real unreal"],
        verse_refs=["gita:7:14", "gita:7:15"],
    ),
    BlockSpec(
        slug="concept/atman-brahman",
        block_type="concept",
        title="Atman and Brahman — Self and Absolute",
        description="Atman: the individual self, not the body or mind. Brahman: the ultimate reality, the ground of all being. The Upanishadic revelation: atman IS Brahman (tat tvam asi). You are not separate from the divine — you ARE the divine, temporarily forgetting. The entire Vedantic project is remembering this.",
        search_queries=["atman brahman self absolute identity", "tat tvam asi you are that Upanishad", "atman brahman Chandogya Mandukya"],
        verse_refs=["gita:2:20", "gita:13:2", "gita:15:7"],
    ),
    BlockSpec(
        slug="concept/samsara",
        block_type="concept",
        title="Samsara — The Cycle of Birth and Death",
        description="Samsara: the wheel of birth, death, and rebirth driven by karma and desire. Not punishment — consequence of unresolved attachments. The soul (atman) transmigrates through bodies until liberation (moksha). The Gita: 'as a person casts off worn-out garments and puts on new ones, so the soul casts off bodies.'",
        search_queries=["samsara cycle birth death rebirth", "transmigration soul body garment Gita", "samsara wheel karma desire liberation"],
        verse_refs=["gita:2:22", "gita:2:13", "gita:8:15"],
    ),
    BlockSpec(
        slug="concept/yoga",
        block_type="concept",
        title="Yoga — Union, Discipline, Path",
        description="Yoga: not just postures — union of individual consciousness with the universal. The Gita defines yoga as 'skill in action' (yogah karmasu kaushalam) and 'equanimity' (samatvam yoga uchyate). Four classical paths: karma (action), jnana (knowledge), bhakti (devotion), raja (meditation). All lead to the same summit.",
        search_queries=["yoga union discipline paths four", "karma jnana bhakti raja yoga", "yoga skill action equanimity Gita"],
        verse_refs=["gita:2:48", "gita:2:50", "gita:6:23"],
    ),
    BlockSpec(
        slug="concept/ahimsa",
        block_type="concept",
        title="Ahimsa — Non-Violence",
        description="Ahimsa: non-violence in thought, word, and deed. The highest dharma (ahimsa paramo dharma). But the Mahabharata adds complexity: when violence is needed to protect the innocent, is non-violence still dharma? Arjuna's dilemma. Gandhi's interpretation vs the Gita's battlefield context.",
        search_queries=["ahimsa non-violence highest dharma", "ahimsa paramo dharma Mahabharata", "violence protection innocent dharma conflict"],
        verse_refs=["gita:16:2", "gita:2:31"],
    ),
    BlockSpec(
        slug="concept/gunas",
        block_type="concept",
        title="Three Gunas — Sattva, Rajas, Tamas",
        description="Gunas: three fundamental qualities that constitute all of nature (prakriti). Sattva (clarity, harmony), rajas (passion, activity), tamas (inertia, darkness). Everything — food, action, knowledge, faith — has a guna profile. Liberation is transcending all three, not just choosing sattva.",
        search_queries=["gunas sattva rajas tamas three qualities", "gunas prakriti nature constitution", "transcend gunas liberation Gita"],
        verse_refs=["gita:14:5", "gita:14:10", "gita:14:25"],
    ),
    BlockSpec(
        slug="concept/varna-jati",
        block_type="concept",
        title="Varna and Jati — Quality vs Birth",
        description="Varna: the four-fold classification (brahmana, kshatriya, vaishya, shudra). The Gita says it's based on guna and karma (quality and action), NOT birth. Jati: the hereditary caste system that developed later. Critical distinction: the texts describe varna as functional classification, not as birth-based hierarchy. Modern caste discrimination has no scriptural basis in the Gita's framework.",
        search_queries=["varna caste guna karma birth Gita", "catur varnyam maya srishtam guna karma", "caste discrimination varna jati difference"],
        verse_refs=["gita:4:13", "gita:18:41"],
    ),
    BlockSpec(
        slug="concept/deva-asura",
        block_type="concept",
        title="Devas and Asuras — Gods and Demons",
        description="Devas: celestial beings who maintain cosmic order (rain, seasons, natural law). NOT morally perfect — Indra's flaws, Agni's desires. Asuras: powerful beings who oppose cosmic order, often through ego and desire for power. The distinction is functional, not moral. The Gita lists divine (daivi) and demonic (asuri) qualities that exist in every human.",
        search_queries=["deva asura gods demons cosmic order", "divine demonic qualities Gita daivi asuri", "devas imperfect Indra flawed cosmic function"],
        verse_refs=["gita:16:1", "gita:16:4", "gita:16:6"],
    ),
    BlockSpec(
        slug="concept/bhakti",
        block_type="concept",
        title="Bhakti — The Path of Devotion",
        description="Bhakti: loving devotion to God as a path to liberation. Democratized spirituality — no caste, no learning, no austerity required. Just love. Nine forms (navavidha bhakti): listening, singing, remembering, serving, worship, prostration, servitude, friendship, self-surrender. The Alvars, Nayanmars, and Bhakti movement transformed Hinduism.",
        search_queries=["bhakti devotion love God liberation", "navavidha bhakti nine forms", "bhakti movement Alvars Nayanmars"],
        verse_refs=["gita:9:26", "gita:9:34", "gita:12:6"],
    ),
    BlockSpec(
        slug="concept/tapas",
        block_type="concept",
        title="Tapas — Austerity and Inner Fire",
        description="Tapas: literally 'heat' — the burning intensity of focused spiritual practice. Not self-torture but disciplined effort. Parvati's tapas wins Shiva. Dhruva's tapas moves Vishnu. The Gita classifies tapas of body, speech, and mind. True tapas is consistent practice, not dramatic renunciation.",
        search_queries=["tapas austerity penance heat spiritual", "tapas body speech mind Gita three types", "Parvati tapas Shiva Dhruva tapas Vishnu"],
        verse_refs=["gita:17:14", "gita:17:15", "gita:17:16"],
    ),
]


# ---- TEXT OVERVIEW BLOCKS ----

TEXT_BLOCKS = [
    BlockSpec(
        slug="text/bhagavad-gita",
        block_type="text-overview",
        title="Bhagavad Gita — Song of God",
        description="700 verses in 18 chapters. Krishna teaches Arjuna on the battlefield of Kurukshetra. Part of Mahabharata (Bhishma Parva). Covers: karma yoga, jnana yoga, bhakti yoga, the nature of self, the three gunas, the universal form, liberation. The most widely read Hindu text worldwide.",
        search_queries=["Bhagavad Gita overview Krishna Arjuna battlefield", "Gita chapters themes karma jnana bhakti"],
        verse_refs=["gita:1:1", "gita:2:47", "gita:4:7", "gita:11:32", "gita:18:66"],
    ),
    BlockSpec(
        slug="text/mahabharata",
        block_type="text-overview",
        title="Mahabharata — The Great Epic",
        description="100,000+ verses. The longest epic poem in world literature. Vyasa's masterwork. The Pandava-Kaurava war, but much more: Vidura Niti, Bhishma's teachings, Yaksha Prashna, Nala-Damayanti, Savitri-Satyavan. Contains the Bhagavad Gita. 'What is found here may be found elsewhere; what is not found here is found nowhere.'",
        search_queries=["Mahabharata overview epic Pandava Kaurava", "Mahabharata structure parvas contents", "Mahabharata what is found here found nowhere"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="text/ramayana",
        block_type="text-overview",
        title="Ramayana — The Journey of Rama",
        description="24,000 verses by Valmiki. Rama's exile, Sita's abduction, the war against Ravana. Seven kandas (books). Not just adventure — a study of ideal conduct (maryada), the cost of duty, and whether dharma demands too much. Two major versions: Valmiki (older, grittier) and Tulsidas (devotional, Hindi).",
        search_queries=["Ramayana overview Valmiki Rama exile Lanka", "Ramayana seven kandas structure", "Ramayana Valmiki Tulsidas versions"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="text/upanishads",
        block_type="text-overview",
        title="Upanishads — The Philosophical Core",
        description="108+ texts, 10-13 considered principal. End portion of the Vedas (Vedanta = 'end of the Vedas'). The philosophical foundation: atman, Brahman, maya, moksha. Key Upanishads: Isha (all is Brahman), Kena (who moves the mind?), Katha (Nachiketa and Death), Mandukya (AUM and four states), Chandogya (tat tvam asi), Brihadaranyaka (neti neti).",
        search_queries=["Upanishads overview principal philosophical", "Upanishads Isha Kena Katha Mandukya Chandogya", "Vedanta end Vedas atman Brahman"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="text/yoga-vasistha",
        block_type="text-overview",
        title="Yoga Vasistha — The Supreme Yoga",
        description="32,000 verses. Sage Vasistha teaches young Rama about the nature of reality, consciousness, and self-effort. Radical Advaita: the world is a dream, the mind creates reality, self-effort trumps destiny. Contains extraordinary nested stories (Queen Lila, the hundred Rudras). The most sophisticated philosophical text in Hindu literature.",
        search_queries=["Yoga Vasistha overview Vasistha Rama consciousness", "Yoga Vasistha self-effort destiny mind reality", "Yoga Vasistha Queen Lila dream world"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="text/arthashastra",
        block_type="text-overview",
        title="Arthashastra — The Science of Statecraft",
        description="Kautilya/Chanakya's treatise on governance, economics, military strategy, law. Pragmatic, not idealistic. Written for rulers who must govern in an imperfect world. Often compared to Machiavelli but more systematic. Covers: treasury, army, spies, diplomacy, law, agriculture, trade. Brutally honest about power.",
        search_queries=["Arthashastra Kautilya Chanakya statecraft governance", "Arthashastra economy military law diplomacy", "Chanakya Niti practical wisdom leadership"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="text/panchatantra",
        block_type="text-overview",
        title="Panchatantra — Five Books of Wisdom",
        description="Animal fables teaching practical wisdom. Five books: Mitra Bheda (loss of friends), Mitra Labha (gaining friends), Kakolukiyam (crows and owls — war), Labdhapranasam (loss of gains), Aparikshitakarakam (hasty action). Translated into 50+ languages — one of the most translated works in human history. Teaches through entertainment.",
        search_queries=["Panchatantra fables five books animals wisdom", "Panchatantra Vishnu Sharma stories practical"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="text/puranas",
        block_type="text-overview",
        title="Puranas — Ancient Stories",
        description="18 Mahapuranas + 18 Upapuranas. Cosmology, genealogies, dharma, pilgrimage, philosophy — embedded in narrative. Key Puranas: Vishnu Purana (Vishnu's incarnations), Bhagavata Purana (Krishna's life, Prahlada), Shiva Purana (Shiva's forms), Markandeya Purana (Devi Mahatmyam). The Puranas made Vedantic philosophy accessible to everyone.",
        search_queries=["Puranas overview eighteen cosmology narrative", "Vishnu Purana Bhagavata Shiva Purana Markandeya", "Puranas dharma stories genealogy"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="text/vedas",
        block_type="text-overview",
        title="Vedas — The Eternal Knowledge",
        description="Four Vedas: Rig (hymns), Yajur (rituals), Sama (chants), Atharva (practical). The oldest sacred texts, considered apaurusheya (authorless, eternal). Each Veda has four parts: Samhitas (hymns), Brahmanas (rituals), Aranyakas (forest texts), Upanishads (philosophy). The foundation of all Hindu thought.",
        search_queries=["Vedas four Rig Yajur Sama Atharva overview", "Vedas Samhita Brahmana Aranyaka Upanishad structure", "Vedas apaurusheya eternal knowledge"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="text/tirukural",
        block_type="text-overview",
        title="Tirukural — The Universal Ethics",
        description="1,330 couplets by Thiruvalluvar in Tamil. Three books: virtue (aram), wealth (porul), love (inbam). No sectarian affiliation — claimed by Hindus, Jains, and secular humanists alike. Pure ethical philosophy. 'Straighter than a measuring rod' — direct, practical, no mythology needed.",
        search_queries=["Tirukural Thiruvalluvar Tamil ethics virtue", "Kural three books aram porul inbam"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="text/devi-mahatmyam",
        block_type="text-overview",
        title="Devi Mahatmyam — Glory of the Goddess",
        description="700 verses from the Markandeya Purana. The Divine Mother's battles against Mahishasura, Shumbha-Nishumbha, Raktabija. Not just mythology — each demon represents an internal obstacle (ego, attachment, multiplying thoughts). The Goddess combines all the gods' powers. Central text of Shakta tradition, recited during Navaratri.",
        search_queries=["Devi Mahatmyam Goddess battles demons Mahishasura", "Durga Saptashati Markandeya Purana divine feminine", "Navaratri Devi Mahatmyam chanting"],
        verse_refs=[],
    ),
]


# ---- LLM GENERATION ----

def get_chromadb_client(chromadb_dir: str) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=chromadb_dir)


def fetch_verses(client: chromadb.ClientAPI, queries: list[str], refs: list[str], limit: int = 3) -> str:
    all_collections = [
        "gita", "ramayana", "mahabharata", "mahabharata_english", "niti",
        "vedanta-texts", "shastras", "devotional", "buddhist",
        "wisdom-stories", "ethics", "upanishads", "vedas", "puranas",
    ]
    parts = []
    for query in queries[:3]:
        for coll_name in all_collections:
            try:
                coll = client.get_collection(coll_name)
                results = coll.query(query_texts=[query], n_results=limit, include=["documents", "metadatas"])
                for i, doc in enumerate(results["documents"][0]):
                    vid = results["metadatas"][0][i].get("verse_id", "unknown")
                    lines = doc.split("\n")
                    sanskrit = next((l for l in lines if l.startswith("Sanskrit:")), "")
                    translation = next((l for l in lines if l.startswith("Translation [")), "")
                    if sanskrit or translation:
                        parts.append(f"[{coll_name}:{vid}]\n{sanskrit}\n{translation}")
            except Exception:
                continue

    for ref in refs[:5]:
        family = ref.split(":")[0]
        try:
            coll = client.get_collection(family)
            results = coll.get(ids=[ref], include=["documents"])
            if results["ids"]:
                doc = results["documents"][0]
                lines = doc.split("\n")
                sanskrit = next((l for l in lines if l.startswith("Sanskrit:")), "")
                translation = next((l for l in lines if l.startswith("Translation [")), "")
                parts.append(f"[{ref}]\n{sanskrit}\n{translation}")
        except Exception:
            continue

    result = []
    total = 0
    for p in parts:
        if total + len(p) > 4000:
            break
        result.append(p)
        total += len(p)
    return "\n\n".join(result)


def generate_block(spec: BlockSpec, verse_context: str, api_key: str, model: str) -> str:
    prompts = {
        "character": f"""Create a CHARACTER BLOCK for: "{spec.title}"
Description: {spec.description}

VERSE CONTEXT FROM SCRIPTURE:
{verse_context}

Write in this exact format:
---
title: {spec.title}
type: character
tags: [relevant, tags, here]
---

## Identity
[Who they are — 2-3 sentences. Role, family, epithet, significance.]

## Key Stories
[List 4-6 key episodes/stories involving this character, each as a
paragraph. What happened, what they did, what it reveals about them.
Include verse references from the context provided.]

## Flaws and Virtues
### Virtues
- [strength 1 with example]
- [strength 2 with example]
### Flaws
- [flaw 1 with example]
- [flaw 2 with example]

## Epithets and Names
- [Name]: [meaning]

## Relationships
- [Character]: [relationship and significance]

## Key Verses
- [verse:ref] — "[quote]" — [context]

## Why They Matter
[What this character teaches us about being human. 2-3 sentences.]

## Common Questions
- [question people ask about this character]
- [question]
- [question]

RULES:
- Use actual verse references from the context provided
- Be honest about flaws — the texts are
- Make it accessible to someone unfamiliar
- Include emotional and narrative detail""",

        "concept": f"""Create a CONCEPT BLOCK for: "{spec.title}"
Description: {spec.description}

VERSE CONTEXT FROM SCRIPTURE:
{verse_context}

Write in this exact format:
---
title: {spec.title}
type: concept
tags: [relevant, tags, here]
---

## Definition
[What this concept means — clear, simple, 2-3 sentences. Avoid
jargon. If the concept is untranslatable, say why.]

## How the Texts Explain It
[Key verses and passages that define this concept. Include the
Sanskrit and translation. 3-4 paragraphs drawing from different
texts.]

## Darshana Perspectives
### Advaita (Non-dual)
[How Shankara's tradition interprets this]
### Vishishtadvaita (Qualified non-dual)
[How Ramanuja's tradition interprets this]
### Dvaita (Dual)
[How Madhva's tradition interprets this]

## Common Misunderstandings
- [misconception 1 and correction]
- [misconception 2 and correction]

## Practical Application
[How this concept applies to everyday life. Concrete examples.]

## Key Verses
- [verse:ref] — "[quote]" — [which text, why it matters]

## Related Concepts
- [Concept]: [how they connect]

RULES:
- Use actual verse references from the context provided
- Present darshana differences honestly, not as one being "right"
- Ground abstract concepts in practical examples
- Sanskrit terms used as-is with explanation""",

        "text-overview": f"""Create a TEXT OVERVIEW BLOCK for: "{spec.title}"
Description: {spec.description}

VERSE CONTEXT FROM SCRIPTURE:
{verse_context}

Write in this exact format:
---
title: {spec.title}
type: text-overview
tags: [relevant, tags, here]
---

## What It Is
[2-3 sentences: what this text is, who wrote it, when, how long.]

## Structure
[How the text is organized — books, chapters, sections. Brief.]

## Core Themes
- [theme 1]: [one-line explanation]
- [theme 2]: [one-line explanation]
- [theme 3]: [one-line explanation]

## Key Passages
[3-4 famous verses or passages from this text, with Sanskrit
and translation. The ones everyone should know.]

## Why It Matters
[What this text contributes that no other text does. 2-3 sentences.]

## Best Starting Points
[Where should someone new to this text begin reading? Specific
chapters or sections.]

## Key Characters (if narrative)
- [Character]: [role in this text]

## Connected Texts
- [Text]: [how they relate]

RULES:
- Use actual verse references from the context provided
- Be specific about structure (book numbers, verse counts)
- Suggest genuine starting points, not "read the whole thing"
- Make it useful as a reference for someone exploring""",
    }

    prompt = prompts[spec.block_type]

    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 16000,
        },
        timeout=300.0,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"].get("content")
    if content is None:
        finish = data["choices"][0].get("finish_reason", "unknown")
        print(f"  WARNING: null content, finish_reason={finish}")
        reasoning = data["choices"][0]["message"].get("reasoning_content", "")
        if reasoning:
            return reasoning
        return f"[Generation failed: finish_reason={finish}]"
    return content


def write_to_gbrain(slug: str, content: str, dry_run: bool = False) -> bool:
    if dry_run:
        print(f"  [dry-run] would write {len(content)} chars to gbrain:{slug}")
        return True
    try:
        result = subprocess.run(
            ["gbrain", "put", slug],
            input=content,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            status = "created"
            try:
                data = json.loads(result.stdout)
                status = data.get("status", "unknown")
            except (json.JSONDecodeError, KeyError):
                pass
            print(f"  -> gbrain: {status}")
            return True
        else:
            print(f"  -> gbrain error: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"  -> gbrain error: {e}")
        return False


def get_existing_slugs() -> set[str]:
    try:
        result = subprocess.run(
            ["gbrain", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {line.split("\t")[0] for line in result.stdout.strip().split("\n") if line}
    except Exception:
        return set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build character, concept, and text-overview blocks")
    parser.add_argument("--chromadb-dir", default=os.environ.get("VEDANTA_CHROMADB_DIR", "./knowledge/chromadb"))
    parser.add_argument("--model", default="moonshotai/kimi-k2.6")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--type", choices=["character", "concept", "text", "all"], default="all")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("Error: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    chromadb_dir = Path(args.chromadb_dir)
    if not chromadb_dir.exists():
        print(f"Error: ChromaDB not found at {chromadb_dir}", file=sys.stderr)
        sys.exit(1)

    client = get_chromadb_client(str(chromadb_dir))
    existing = get_existing_slugs() if args.skip_existing else set()

    blocks: list[BlockSpec] = []
    if args.type in ("character", "all"):
        blocks.extend(CHARACTER_BLOCKS)
    if args.type in ("concept", "all"):
        blocks.extend(CONCEPT_BLOCKS)
    if args.type in ("text", "all"):
        blocks.extend(TEXT_BLOCKS)

    to_process = []
    skipped = 0
    for spec in blocks:
        if spec.slug in existing:
            skipped += 1
        else:
            to_process.append(spec)

    print(f"Building {len(to_process)} blocks with {args.model} (skipping {skipped} existing)")
    print(f"  Characters: {sum(1 for b in to_process if b.block_type == 'character')}")
    print(f"  Concepts: {sum(1 for b in to_process if b.block_type == 'concept')}")
    print(f"  Text overviews: {sum(1 for b in to_process if b.block_type == 'text-overview')}")
    print(f"ChromaDB: {chromadb_dir}")
    print()

    created = 0
    failed = 0

    for i, spec in enumerate(to_process):
        print(f"[{i + 1}/{len(to_process)}] {spec.block_type}: {spec.title}")

        print("  Fetching verse context...")
        verse_context = fetch_verses(client, spec.search_queries, spec.verse_refs)
        if not verse_context:
            print("  WARNING: no verse context found")
            verse_context = "(No verse context available — generate from your knowledge of the texts)"

        print(f"  Generating with {args.model}...")
        content = generate_block(spec, verse_context, api_key, args.model)

        if write_to_gbrain(spec.slug, content, dry_run=args.dry_run):
            created += 1
        else:
            failed += 1

    print(f"\nDone: {created} created, {failed} failed, {skipped} skipped")

    if created > 0 and not args.dry_run:
        print("\nEmbedding all pages...")
        env = os.environ.copy()
        env["OPENAI_API_KEY"] = env.get("OPENROUTER_API_KEY", "")
        env["OPENAI_BASE_URL"] = "https://openrouter.ai/api/v1"
        result = subprocess.run(
            ["gbrain", "embed", "--all"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        print(result.stdout.strip())
        if result.returncode != 0:
            print(f"Embed error: {result.stderr[:200]}")


if __name__ == "__main__":
    main()
