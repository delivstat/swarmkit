# /// script
# requires-python = ">=3.11"
# dependencies = ["chromadb>=0.5", "httpx>=0.27"]
# ///
# ruff: noqa: E501, E402, E741, PLW1510, PLR0915, PLR0912, PLC0415, F841
"""Batch wisdom graph builder — bypasses the agent loop entirely.

For each theme/story, makes ONE LLM call to generate a wisdom or
knowledge block, then writes it to GBrain via CLI. No tool loop,
no growing context, no per-tool limits.

Usage:
    uv run scripts/build-wisdom-graph.py [--chromadb-dir DIR] [--model MODEL] [--dry-run]

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

# Force unbuffered output so background runs show progress
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
    block_type: str  # "wisdom" or "knowledge"
    title: str
    description: str
    search_queries: list[str]
    verse_refs: list[str]


# ---- Block definitions ----

WISDOM_BLOCKS = [
    BlockSpec(
        slug="truth-and-honesty",
        block_type="wisdom",
        title="Truth and Honesty",
        description="Satya as dharma. Yudhishthira's commitment to truth, Harishchandra's story.",
        search_queries=["truth honesty satya dharma", "Yudhishthira truth"],
        verse_refs=["gita:2:16", "gita:16:2", "gita:17:15"],
    ),
    BlockSpec(
        slug="patience-and-endurance",
        block_type="wisdom",
        title="Patience and Endurance",
        description="Tapas, endurance through suffering. Draupadi's exile, Kunti's silent strength.",
        search_queries=["patience endurance tapas suffering", "exile endurance"],
        verse_refs=["gita:2:14", "gita:2:15", "gita:12:13"],
    ),
    BlockSpec(
        slug="relationships-and-family",
        block_type="wisdom",
        title="Relationships and Family",
        description="Family duty vs personal truth. Rama-Bharata, Karna-Kunti, bonds that define dharma.",
        search_queries=["family duty bond relationship", "brother loyalty"],
        verse_refs=["gita:1:28", "gita:1:37", "gita:2:6"],
    ),
    BlockSpec(
        slug="wealth-and-contentment",
        block_type="wisdom",
        title="Wealth and Contentment",
        description="Aparigraha, non-possessiveness. Contentment vs accumulation. Krishna-Sudama contrast.",
        search_queries=["wealth contentment greed possession", "renunciation possessions"],
        verse_refs=["gita:2:55", "gita:4:22", "gita:16:21"],
    ),
    BlockSpec(
        slug="leadership-and-service",
        block_type="wisdom",
        title="Leadership and Service",
        description="Rajadharma, servant leadership. Rama as ideal king, Yudhishthira's burden.",
        search_queries=["leadership king duty service", "rajadharma ruler"],
        verse_refs=["gita:3:21", "gita:3:22", "gita:3:25"],
    ),
    BlockSpec(
        slug="teachers-and-learning",
        block_type="wisdom",
        title="Teachers and Learning",
        description="Guru-shishya tradition. Approaching a teacher with humility. Knowledge as liberation.",
        search_queries=["teacher guru knowledge learning", "guru wisdom disciple"],
        verse_refs=["gita:4:34", "gita:4:38", "gita:4:39"],
    ),
    BlockSpec(
        slug="nature-of-happiness",
        block_type="wisdom",
        title="The Nature of Happiness",
        description="Ananda in Upanishads. Happiness vs pleasure. Lasting joy from within, not from objects.",
        search_queries=["happiness bliss ananda joy pleasure", "inner peace joy"],
        verse_refs=["gita:2:66", "gita:5:21", "gita:6:21"],
    ),
    BlockSpec(
        slug="karma-and-consequences",
        block_type="wisdom",
        title="Karma and Consequences",
        description="Actions have consequences across lifetimes. Karna's tragic arc. Cause and effect.",
        search_queries=["karma consequences action result", "karma fruit action"],
        verse_refs=["gita:4:17", "gita:18:12", "gita:3:13"],
    ),
    BlockSpec(
        slug="non-violence-and-compassion",
        block_type="wisdom",
        title="Non-Violence and Compassion",
        description="Ahimsa. Compassion for all beings. The tension between non-violence and righteous action.",
        search_queries=["non-violence compassion ahimsa beings", "compassion creatures"],
        verse_refs=["gita:16:2", "gita:12:13", "gita:6:32"],
    ),
    BlockSpec(
        slug="desire-and-attachment",
        block_type="wisdom",
        title="Desire and Attachment",
        description="Kama and raga. How desire binds, how detachment frees. Not suppression but understanding.",
        search_queries=["desire attachment craving binding", "desire enemy"],
        verse_refs=["gita:2:62", "gita:2:63", "gita:3:37"],
    ),
    BlockSpec(
        slug="food-and-discipline",
        block_type="wisdom",
        title="Food, Health and Discipline",
        description="Sattvic/rajasic/tamasic food. Discipline of body and senses. Moderation in all things.",
        search_queries=["food discipline senses body health", "sattvic food"],
        verse_refs=["gita:6:16", "gita:6:17", "gita:17:8"],
    ),
    BlockSpec(
        slug="women-in-dharma",
        block_type="wisdom",
        title="Women and Dharma",
        description="Sita's strength, Draupadi's fire, Savitri's wisdom, Kunti's sacrifice. Women as dharma-keepers.",
        search_queries=["women strength dharma Sita Draupadi", "feminine power"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="friendship",
        block_type="wisdom",
        title="Friendship",
        description="Krishna-Arjuna, Krishna-Sudama, Karna-Duryodhana. Bonds that transcend status.",
        search_queries=["friendship bond companion loyalty", "friend companion"],
        verse_refs=["gita:4:3", "gita:9:18", "gita:11:41"],
    ),
    BlockSpec(
        slug="old-age-and-wisdom",
        block_type="wisdom",
        title="Old Age and Wisdom",
        description="Bhishma's final teachings. What to let go of. Legacy and acceptance.",
        search_queries=["old age wisdom death legacy", "elder teaching"],
        verse_refs=["gita:2:13", "gita:2:22", "gita:8:5"],
    ),
    BlockSpec(
        slug="nature-and-environment",
        block_type="wisdom",
        title="Nature and the Divine",
        description="God manifests as nature. Rivers, mountains, trees as sacred. Ecological awareness in texts.",
        search_queries=["nature divine creation earth trees", "creation nature"],
        verse_refs=["gita:10:25", "gita:10:31", "gita:10:35"],
    ),
    BlockSpec(
        slug="jealousy-and-comparison",
        block_type="wisdom",
        title="Jealousy and Comparison",
        description="Comparing yourself to others. Duryodhana's envy of the Pandavas. Why comparison is the thief of peace.",
        search_queries=["jealousy envy comparison others", "Duryodhana envy"],
        verse_refs=["gita:16:4", "gita:16:18", "gita:2:57"],
    ),
    BlockSpec(
        slug="loneliness-and-isolation",
        block_type="wisdom",
        title="Loneliness and Isolation",
        description="Feeling alone. Rama's exile, Sita's captivity, the hermit's path. Solitude vs loneliness.",
        search_queries=["loneliness alone isolation solitude", "exile alone forest"],
        verse_refs=["gita:6:10", "gita:13:10"],
    ),
    BlockSpec(
        slug="marriage-and-partnership",
        block_type="wisdom",
        title="Marriage and Partnership",
        description="Dharma in partnership. Rama-Sita, Shiva-Parvati, Nala-Damayanti. Love, duty, and sacrifice in marriage.",
        search_queries=["marriage partner love wife husband", "couple dharma partnership"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="parenting-and-children",
        block_type="wisdom",
        title="Parenting and Raising Children",
        description="Duty of parents. Dasharatha's sacrifice, Kunti's impossible choices, Dhritarashtra's blind love.",
        search_queries=["parent child son daughter raising", "father mother child duty"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="addiction-and-self-destruction",
        block_type="wisdom",
        title="Addiction and Self-Destructive Habits",
        description="Yudhishthira's gambling addiction destroyed a kingdom. Desire as the enemy. Breaking chains of compulsion.",
        search_queries=["addiction desire compulsion gambling", "desire enemy binding"],
        verse_refs=["gita:3:37", "gita:3:39", "gita:3:43"],
    ),
    BlockSpec(
        slug="gratitude-and-appreciation",
        block_type="wisdom",
        title="Gratitude and Appreciation",
        description="Recognizing what you have. Sudama's contentment, Shabari's decades of patient waiting. Abundance mindset.",
        search_queries=["gratitude thankful appreciation contentment", "grateful blessings"],
        verse_refs=["gita:9:26", "gita:12:13"],
    ),
    BlockSpec(
        slug="time-and-mortality",
        block_type="wisdom",
        title="Time and Mortality",
        description="Life is short. Krishna as Time (kala). Urgency of living with purpose. Don't postpone dharma.",
        search_queries=["time death mortality urgency life", "kala time destroyer"],
        verse_refs=["gita:11:32", "gita:2:27"],
    ),
    BlockSpec(
        slug="letting-go-of-past",
        block_type="wisdom",
        title="Letting Go of the Past",
        description="Releasing old wounds, grudges, regrets. The past is gone. Kunti releasing Karna, Draupadi after the war.",
        search_queries=["past regret release letting go", "forgiveness past wounds"],
        verse_refs=["gita:2:11", "gita:2:14"],
    ),
    BlockSpec(
        slug="self-forgiveness",
        block_type="wisdom",
        title="Forgiving Yourself",
        description="Even sinners can cross the ocean of sin. Self-compassion. Arjuna's shame, Valmiki's transformation.",
        search_queries=["self forgiveness sin redemption", "sinner cross ocean forgive"],
        verse_refs=["gita:4:36", "gita:9:30", "gita:18:66"],
    ),
    BlockSpec(
        slug="trust-and-betrayal",
        block_type="wisdom",
        title="Trust and Betrayal",
        description="Broken trust. Drona's betrayal of Ekalavya, Shakuni's manipulation, Vibhishana leaving Ravana for dharma.",
        search_queries=["trust betrayal loyalty broken deception", "betrayal loyalty dharma"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="success-and-emptiness",
        block_type="wisdom",
        title="Success and Its Emptiness",
        description="Achieving everything and feeling nothing. Yudhishthira's hollow victory. When winning feels like losing.",
        search_queries=["success empty hollow victory meaningless", "victory grief hollow"],
        verse_refs=["gita:2:8", "gita:18:17"],
    ),
    BlockSpec(
        slug="tat-tvam-asi",
        block_type="wisdom",
        title="Tat Tvam Asi — You Are That",
        description="The Chandogya Upanishad's mahavakya. The individual self is identical with the universal Self. Non-duality in daily life.",
        search_queries=["tat tvam asi you are that self", "atman brahman identity"],
        verse_refs=["gita:13:2", "gita:10:20"],
    ),
    BlockSpec(
        slug="neti-neti",
        block_type="wisdom",
        title="Neti Neti — Not This, Not This",
        description="Brihadaranyaka Upanishad. Knowing the Self by negation — stripping away everything that is not-Self until only truth remains.",
        search_queries=["neti neti not this self negation", "what is not self"],
        verse_refs=["gita:13:12", "gita:2:16"],
    ),
    BlockSpec(
        slug="five-sheaths",
        block_type="wisdom",
        title="The Five Sheaths (Pancha Kosha)",
        description="Taittiriya Upanishad. Five layers from gross body to bliss: annamaya, pranamaya, manomaya, vijnanamaya, anandamaya.",
        search_queries=["five sheaths kosha body mind bliss", "layers self body"],
        verse_refs=["gita:13:1", "gita:13:2"],
    ),
    BlockSpec(
        slug="three-states-consciousness",
        block_type="wisdom",
        title="Three States of Consciousness",
        description="Mandukya Upanishad. Waking, dreaming, deep sleep — and turiya, the fourth state that witnesses all three.",
        search_queries=["consciousness waking dreaming sleep awareness", "states consciousness witness"],
        verse_refs=["gita:2:69"],
    ),
    BlockSpec(
        slug="death-of-loved-one",
        block_type="wisdom",
        title="Dealing with Death of a Loved One",
        description="Grief, loss, impermanence. Arjuna's grief over fighting kin, Gandhari's loss of a hundred sons. The soul is never born, never dies.",
        search_queries=["grief death loss impermanence loved one", "soul never dies body perishes"],
        verse_refs=["gita:2:13", "gita:2:22", "gita:2:27"],
    ),
    BlockSpec(
        slug="childhood-trauma-healing",
        block_type="wisdom",
        title="Childhood Trauma and Healing",
        description="Inner wounds, recovery, the child within. Karna abandoned at birth by Kunti, Ekalavya rejected by Drona. Wounds from the past need not define the future.",
        search_queries=["childhood wound abandonment rejection healing", "Karna abandoned Ekalavya rejected"],
        verse_refs=["gita:4:36", "gita:18:66"],
    ),
    BlockSpec(
        slug="sibling-rivalry",
        block_type="wisdom",
        title="Sibling Rivalry",
        description="Competition between brothers and sisters. Pandavas vs Kauravas, Vali-Sugriva, Ravana-Vibhishana. When envy poisons blood bonds.",
        search_queries=["sibling rivalry brothers competition envy", "Pandavas Kauravas brothers conflict"],
        verse_refs=["gita:1:26", "gita:1:37"],
    ),
    BlockSpec(
        slug="spiritual-doubt-crisis-faith",
        block_type="wisdom",
        title="Spiritual Doubt and Crisis of Faith",
        description="Questioning God, losing belief. Arjuna's crisis in chapter 1, Nachiketa's questioning of death. Doubt as a gateway, not a dead end.",
        search_queries=["doubt faith crisis questioning God belief", "Arjuna confusion despair questioning"],
        verse_refs=["gita:2:7", "gita:4:40", "gita:18:66"],
    ),
    BlockSpec(
        slug="purpose-after-retirement",
        block_type="wisdom",
        title="Purpose After Retirement",
        description="What to do when career or role ends. Vanaprastha stage of life — withdrawal from active duties. Bhishma's retirement from active kingship to the role of elder guide.",
        search_queries=["retirement purpose vanaprastha stage elder", "life stage withdrawal forest"],
        verse_refs=["gita:3:35", "gita:18:45"],
    ),
    BlockSpec(
        slug="loneliness-in-marriage",
        block_type="wisdom",
        title="Loneliness in Marriage",
        description="Feeling alone despite being married. Sita's isolation in Lanka surrounded by demons, Gandhari's self-imposed blindfold shutting out the world alongside a blind husband.",
        search_queries=["loneliness marriage alone partner isolation", "Sita Lanka isolation Gandhari blindfold"],
        verse_refs=["gita:6:10", "gita:13:10"],
    ),
    BlockSpec(
        slug="guilt-and-shame",
        block_type="wisdom",
        title="Guilt and Shame",
        description="Carrying the weight of past actions. Arjuna's guilt about killing his own family, Ashwatthama's curse for his unforgivable act. How to move forward when the past weighs heavy.",
        search_queries=["guilt shame past actions weight regret", "Arjuna guilt killing Ashwatthama curse"],
        verse_refs=["gita:2:11", "gita:9:30", "gita:18:66"],
    ),
    BlockSpec(
        slug="letting-go-of-someone",
        block_type="wisdom",
        title="Letting Go of Someone You Love",
        description="Releasing attachment to a person. Kunti releasing Karna at birth, Dasharatha letting Rama go into exile. Love that holds on vs love that lets go.",
        search_queries=["letting go love attachment release person", "Kunti Karna Dasharatha Rama separation"],
        verse_refs=["gita:2:62", "gita:2:71", "gita:12:13"],
    ),
    BlockSpec(
        slug="meaning-in-suffering",
        block_type="wisdom",
        title="Finding Meaning in Suffering",
        description="Why suffering exists, how to grow from it. Tapas as purification through fire. Draupadi's exile and humiliation as the forge that shaped a queen of iron.",
        search_queries=["suffering meaning tapas purification growth", "Draupadi suffering exile humiliation"],
        verse_refs=["gita:2:14", "gita:2:15", "gita:5:22"],
    ),
    BlockSpec(
        slug="duty-to-parents-vs-own-life",
        block_type="wisdom",
        title="Duty to Parents vs Own Life",
        description="Honoring parents while living your truth. Rama's obedience to Dasharatha's word vs his own wishes, Shravana Kumar carrying his blind parents on a pilgrimage.",
        search_queries=["duty parents obedience own life truth", "Rama Dasharatha obedience Shravana Kumar"],
        verse_refs=["gita:3:35", "gita:18:47", "gita:2:31"],
    ),
]

KNOWLEDGE_BLOCKS = [
    BlockSpec(
        slug="bhishma-on-deathbed",
        block_type="knowledge",
        title="Bhishma on the Bed of Arrows",
        description="Bhishma, pierced by countless arrows, lies on a bed of arrows and teaches Yudhishthira about dharma, kingship, and the nature of life. Shanti Parva and Anushasana Parva.",
        search_queries=["Bhishma arrows deathbed teaching", "Shanti Parva"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="karna-story",
        block_type="knowledge",
        title="The Tragedy of Karna",
        description="Karna's full arc: abandoned at birth, raised by a charioteer, rejected by Drona, befriended by Duryodhana, generous to a fault, cursed by fate, dies fighting against his own brothers.",
        search_queries=["Karna birth charioteer Duryodhana", "Karna generosity death"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="krishna-universal-form",
        block_type="knowledge",
        title="Krishna's Universal Form (Vishwarupa)",
        description="In Gita chapter 11, Arjuna asks to see Krishna's true form. Krishna reveals the vishwarupa — all of creation, all of time, all of destruction in one overwhelming vision.",
        search_queries=["vishwarupa universal form Krishna", "divine form chapter 11"],
        verse_refs=["gita:11:9", "gita:11:12", "gita:11:32"],
    ),
    BlockSpec(
        slug="savitri-and-satyavan",
        block_type="knowledge",
        title="Savitri and Satyavan",
        description="Savitri follows Yama, the god of death, when he takes her husband Satyavan's soul. Through wit, devotion, and persistence she wins him back — the only mortal to defeat Death with words.",
        search_queries=["Savitri Satyavan Yama death", "Savitri husband death"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="nachiketa-and-yama",
        block_type="knowledge",
        title="Nachiketa and Yama (Katha Upanishad)",
        description="A boy named Nachiketa is sent to the house of Death by his angry father. Yama offers him any boon. Nachiketa refuses wealth and pleasure — he wants to know: what happens after death?",
        search_queries=["Nachiketa Yama death knowledge", "Katha Upanishad boy"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="shabari-and-rama",
        block_type="knowledge",
        title="Shabari and Rama",
        description="An elderly tribal woman named Shabari waits decades for Rama to visit her hermitage. When he arrives, she offers him berries she has tasted first to ensure sweetness. Rama eats them with love.",
        search_queries=["Shabari Rama berries devotion", "Shabari tribal woman"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vidura-niti",
        block_type="knowledge",
        title="Vidura's Counsel",
        description="Vidura, the wise half-brother, counsels blind king Dhritarashtra throughout the Mahabharata. His niti (statecraft and ethics) covers justice, leadership, human nature, and moral courage.",
        search_queries=["Vidura counsel Dhritarashtra wisdom", "Vidura niti"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="abhimanyu-chakravyuha",
        block_type="knowledge",
        title="Abhimanyu and the Chakravyuha",
        description="Arjuna's teenage son Abhimanyu knew how to enter the deadly Chakravyuha formation but not how to exit. He entered alone, fought seven great warriors, and died — the cost of incomplete knowledge.",
        search_queries=["Abhimanyu Chakravyuha formation", "Abhimanyu death battle"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="damayanti-and-nala",
        block_type="knowledge",
        title="Nala and Damayanti",
        description="King Nala loses his kingdom to gambling (parallels Yudhishthira), is separated from his wife Damayanti. Their reunion is a story of love surviving loss, exile, and deception.",
        search_queries=["Nala Damayanti gambling exile", "Nala kingdom loss"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="bhagiratha-ganga",
        block_type="knowledge",
        title="Bhagiratha Brings the Ganga",
        description="King Bhagiratha performs severe penance to bring the river Ganga from heaven to earth to liberate the souls of his ancestors. Shiva catches her in his locks to prevent destruction.",
        search_queries=["Bhagiratha Ganga heaven penance", "Ganga river Shiva"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="bhima-meets-hanuman",
        block_type="knowledge",
        title="Bhima Meets Hanuman",
        description="During exile, mighty Bhima encounters an old monkey blocking his path. He cannot move its tail. The monkey reveals himself as Hanuman — Bhima's half-brother. Humility of the strong.",
        search_queries=["Bhima Hanuman monkey tail forest", "Bhima meets Hanuman"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="yudhishthira-dog-heaven",
        block_type="knowledge",
        title="Yudhishthira's Dog at Heaven's Gate",
        description="At journey's end, only Yudhishthira reaches heaven alive. A stray dog has followed him. The gods say the dog cannot enter. Yudhishthira refuses to abandon it. The dog is Dharma himself.",
        search_queries=["Yudhishthira dog heaven gate test", "dog heaven dharma"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="rama-and-squirrel",
        block_type="knowledge",
        title="Rama and the Squirrel",
        description="While monkeys build the bridge to Lanka, a tiny squirrel helps by carrying pebbles. Monkeys laugh. Rama picks up the squirrel, strokes its back, and says every contribution matters.",
        search_queries=["Rama squirrel bridge Lanka pebbles", "squirrel small contribution"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="prahlada-and-narasimha",
        block_type="knowledge",
        title="Prahlada and Narasimha",
        description="Young Prahlada worships Vishnu despite his demon father Hiranyakashipu's hatred. His father tries to kill him repeatedly. Vishnu appears as Narasimha — half-man, half-lion — to protect the devotee.",
        search_queries=["Prahlada Narasimha Hiranyakashipu devotion", "Prahlada faith demon"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="dhruva-penance",
        block_type="knowledge",
        title="Dhruva's Penance",
        description="Five-year-old Dhruva, rejected by his father the king, goes to the forest to find God. His determination is so fierce that Vishnu himself appears. Dhruva becomes the Pole Star — immovable.",
        search_queries=["Dhruva child penance star determination", "Dhruva boy forest Vishnu"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="ashtavakra-and-janaka",
        block_type="knowledge",
        title="Ashtavakra and King Janaka",
        description="Ashtavakra, a boy born with eight deformities, enters King Janaka's court. The scholars laugh at his body. Ashtavakra says: you are shoe-makers who judge by skin, not by wisdom. He defeats them all.",
        search_queries=["Ashtavakra Janaka deformed wisdom court", "Ashtavakra debate body"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="krishna-lifts-govardhan",
        block_type="knowledge",
        title="Krishna Lifts Govardhan",
        description="When Indra floods Vrindavan in rage, young Krishna lifts the entire Govardhan mountain on his little finger to shelter the villagers. Community protection through divine courage.",
        search_queries=["Krishna Govardhan mountain Indra rain", "Krishna lifts mountain"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="shiva-and-parvati",
        block_type="knowledge",
        title="Shiva and Parvati",
        description="Parvati's devotion to win Shiva as her husband. Years of penance, rejection, testing. The divine marriage as a model of equal partnership between masculine stillness and feminine energy.",
        search_queries=["Shiva Parvati marriage devotion penance", "Parvati tapas Shiva"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="valmiki-transformation",
        block_type="knowledge",
        title="Valmiki's Transformation",
        description="Valmiki was a highway robber named Ratnakara. A sage asked him: will your family share the sin of your crimes? They refused. Shattered, he meditated so long that an anthill grew over him. He emerged as Valmiki — and wrote the Ramayana.",
        search_queries=["Valmiki robber transformation sage", "Ratnakara anthill Ramayana"],
        verse_refs=[],
    ),
    # ---- New texts: Yoga Vasistha ----
    BlockSpec(
        slug="nature-of-mind-vasistha",
        block_type="wisdom",
        title="Nature of Mind (Yoga Vasistha)",
        description="The mind alone is the cause of bondage and liberation. Vasistha teaches Rama that the world is as you see it — change the mind, change the world. No external practice needed if the mind is still.",
        search_queries=["mind bondage liberation Vasistha", "mind cause world illusion"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="self-effort-vs-destiny",
        block_type="wisdom",
        title="Self-effort vs Destiny (Yoga Vasistha)",
        description="Vasistha's core teaching: self-effort (purushartha) always prevails over fate. Destiny is the result of past effort. Present effort can overcome past karma. Laziness disguised as surrender is not spirituality.",
        search_queries=["self effort destiny fate purushartha", "effort overcomes fate Vasistha"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="world-as-dream",
        block_type="wisdom",
        title="World as Dream (Yoga Vasistha)",
        description="The Yoga Vasistha's radical teaching: waking life has the same ontological status as a dream. Neither real nor unreal — experienced but not substantial. Liberation is waking up from the dream of separation.",
        search_queries=["world dream waking illusion Vasistha", "dream waking reality consciousness"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="story-of-queen-lila",
        block_type="knowledge",
        title="Story of Queen Lila (Yoga Vasistha)",
        description="Queen Lila's husband dies. Through meditation, she discovers he is living a completely different life in another reality — simultaneously. Vasistha uses this story to teach that consciousness creates multiple simultaneous worlds, and death is merely a transition between them.",
        search_queries=["Queen Lila Vasistha husband death parallel", "Lila consciousness worlds"],
        verse_refs=[],
    ),
    # ---- New texts: Brahma Sutras ----
    BlockSpec(
        slug="brahman-inquiry",
        block_type="wisdom",
        title="The Inquiry into Brahman (Brahma Sutras)",
        description="Athato brahma jijnasa — now therefore the inquiry into Brahman. The Brahma Sutras systematize the Upanishadic teaching that Brahman is the origin, sustainer, and dissolution of the universe. Not intellectual knowledge but direct realization.",
        search_queries=["Brahman inquiry jijnasa Brahma Sutra", "athato brahma origin universe"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="individual-self-and-brahman",
        block_type="wisdom",
        title="Individual Self and Brahman (Brahma Sutras)",
        description="The jiva (individual self) is not different from Brahman — it only appears so due to upadhis (limiting adjuncts). Like space in a pot is not different from universal space. Moksha is recognizing this identity, not achieving something new.",
        search_queries=["jiva Brahman identity upadhi", "individual self universal Brahma Sutra"],
        verse_refs=[],
    ),
    # ---- New texts: Ashtavakra Gita ----
    BlockSpec(
        slug="ashtavakra-instant-liberation",
        block_type="wisdom",
        title="Instant Liberation (Ashtavakra Gita)",
        description="Ashtavakra tells Janaka: you are already free. Liberation is not something to be attained through practice — it is your nature right now. The very idea that you are bound is the only bondage. Drop that idea and you are free this instant.",
        search_queries=["Ashtavakra liberation free instant", "already free bondage idea Janaka"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="witness-consciousness",
        block_type="wisdom",
        title="Witness Consciousness (Ashtavakra Gita)",
        description="You are the witness — pure awareness watching thoughts, emotions, and the body. You are not the doer. Like the sky is not affected by clouds passing through it, you are not affected by experiences. Rest as the witness.",
        search_queries=["witness awareness Ashtavakra consciousness", "not doer pure awareness sky clouds"],
        verse_refs=[],
    ),
    # ---- New texts: Arthashastra ----
    BlockSpec(
        slug="practical-wisdom-governance",
        block_type="wisdom",
        title="Practical Wisdom for Leadership (Arthashastra)",
        description="Kautilya's teaching: a leader must balance dharma (righteousness), artha (wealth), and kama (desire). Self-discipline is the foundation of all governance — one who cannot govern themselves cannot govern others. The four upayas: sama (persuasion), dana (giving), bheda (division), danda (force).",
        search_queries=["leadership governance dharma artha kama", "self discipline leader Kautilya Arthashastra"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="strategic-patience",
        block_type="wisdom",
        title="Strategic Patience (Arthashastra)",
        description="Kautilya teaches that the wise person waits for the right moment. Not every battle should be fought immediately. Sometimes restraint is the greatest strategy. The tortoise withdrawing into its shell is not cowardice — it is wisdom.",
        search_queries=["patience strategy wait right moment Kautilya", "restraint wisdom timing Arthashastra"],
        verse_refs=[],
    ),
    # ---- New texts: Panchatantra ----
    BlockSpec(
        slug="practical-wisdom-fables",
        block_type="wisdom",
        title="Practical Wisdom through Stories (Panchatantra)",
        description="The Panchatantra teaches through animal fables: the crow who used pebbles to raise water, the monkey who destroyed the bridge it sat on. Wisdom is not just knowing the truth — it is knowing when and how to act on it. Better to be clever than merely strong.",
        search_queries=["Panchatantra wisdom fable practical clever", "animal story strategy Panchatantra"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="choosing-friends-wisely",
        block_type="wisdom",
        title="Choosing Friends Wisely (Panchatantra & Hitopadesha)",
        description="Both the Panchatantra and Hitopadesha emphasize: your association determines your destiny. The company of fools brings ruin even to the wise. True friendship is tested in adversity, not prosperity. A friend who tells you the truth you don't want to hear is worth more than one who flatters.",
        search_queries=["friendship company wise fools Panchatantra", "true friend adversity Hitopadesha counsel"],
        verse_refs=[],
    ),
    # ---- New texts: Dhammapada ----
    BlockSpec(
        slug="mind-is-everything-dhammapada",
        block_type="wisdom",
        title="Mind is Everything (Dhammapada)",
        description="All that we are is the result of what we have thought. The mind is everything. What you think, you become. If you speak or act with a pure mind, happiness follows like a shadow that never leaves. The Dhammapada's opening verses are among the most practical teachings on mental discipline.",
        search_queries=["mind everything thought Dhammapada", "pure mind happiness follows shadow"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="overcoming-hatred",
        block_type="wisdom",
        title="Overcoming Hatred (Dhammapada)",
        description="Hatred does not cease by hatred at any time. Hatred ceases by love. This is an eternal law. The Dhammapada teaches that anger and resentment are poisons you drink hoping the other person dies. Letting go is not weakness — it is the ultimate strength.",
        search_queries=["hatred ceases love Dhammapada", "anger resentment letting go eternal law"],
        verse_refs=[],
    ),
    # ---- New texts: Devi Mahatmyam (Markandeya Purana) ----
    BlockSpec(
        slug="divine-feminine-strength",
        block_type="wisdom",
        title="Divine Feminine Strength (Devi Mahatmyam)",
        description="When the gods could not defeat the demons, the Divine Mother arose from their combined powers. She teaches: sometimes the gentlest force is the most powerful. Inner strength is not aggression — it is the unshakeable calm of someone who knows their own power.",
        search_queries=["Devi Divine Mother strength power demons", "feminine strength calm power Mahatmyam"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="overcoming-inner-demons",
        block_type="wisdom",
        title="Overcoming Inner Demons (Devi Mahatmyam)",
        description="The asuras (demons) in the Devi Mahatmyam represent internal obstacles: Mahishasura is ego, Shumbha and Nishumbha are attachment and aversion, Raktabija is the thought that multiplies when you fight it. The Goddess defeats them not by force alone but by transformation.",
        search_queries=["demons obstacles ego attachment Devi Mahatmyam", "Mahishasura ego inner demons transformation"],
        verse_refs=[],
    ),
    # ---- New texts: Tirukural ----
    BlockSpec(
        slug="tirukural-virtue-ethics",
        block_type="wisdom",
        title="Universal Ethics (Tirukural)",
        description="Thiruvalluvar's 1,330 couplets cover virtue, wealth, and love without referencing any specific religion. His teaching: virtue is the foundation of all prosperity. Speak truth, practice non-violence, be generous, control desire. These are universal — they belong to no tradition and every tradition.",
        search_queries=["Tirukural virtue ethics universal Thiruvalluvar", "Kural truth non-violence generosity"],
        verse_refs=[],
    ),
]


def get_chromadb_client(chromadb_dir: str) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=chromadb_dir)


def fetch_verses(client: chromadb.ClientAPI, queries: list[str], refs: list[str], limit: int = 3) -> str:
    """Fetch verse context from ChromaDB — summary level."""
    parts = []

    all_collections = [
        "gita", "ramayana", "mahabharata", "niti",
        "vedanta-texts", "shastras", "devotional", "buddhist",
        "wisdom-stories", "ethics", "upanishads", "vedas",
    ]
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
                        parts.append(f"[{vid}]\n{sanskrit}\n{translation}")
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
                transliteration = next((l for l in lines if l.startswith("Transliteration:")), "")
                translation = next((l for l in lines if l.startswith("Translation [")), "")
                speaker = next((l for l in lines if l.startswith("Speaker:")), "")
                parts.append(f"[{ref}]\n{sanskrit}\n{transliteration}\n{speaker}\n{translation}")
        except Exception:
            continue

    # Limit total context to ~3000 chars to avoid timeouts
    result = []
    total = 0
    for p in parts:
        if total + len(p) > 3000:
            break
        result.append(p)
        total += len(p)
    return "\n\n".join(result)


def generate_block(spec: BlockSpec, verse_context: str, api_key: str, model: str) -> str:
    """One LLM call to generate a complete block."""

    if spec.block_type == "wisdom":
        prompt = f"""Create a WISDOM BLOCK for the theme: "{spec.title}"
Description: {spec.description}

VERSE CONTEXT FROM SCRIPTURE:
{verse_context}

Write in this exact format:
---
title: {spec.title}
type: wisdom
tags: [relevant, tags, here]
---

## Core Teaching
[Synthesized teaching from the verses above. 2-3 paragraphs.]

## Darshana Perspectives
### Karma Yoga
[Interpretation through action lens]
### Advaita
[Non-dual interpretation]
### Bhakti
[Devotion interpretation]

## Applicable When
- [specific life situation 1]
- [specific life situation 2]
- [specific life situation 3]
- [specific life situation 4]
- [specific life situation 5]

## Emotional States
- [emotion 1]
- [emotion 2]
- [emotion 3]

## Depth Levels
### Practical
[One-line actionable advice]
### Philosophical
[Deeper exploration]
### Contemplative
[Pointer for meditation/inquiry]

## Teaching Story
[A narrative from Mahabharata/Ramayana that illustrates this teaching. Tell it as a story, not a summary. 3-5 paragraphs.]

## Source Verses
- [verse:ref] — "[brief quote]" [primary/supporting/cross-text]

## Counter-Teaching
[What appears to contradict this, with resolution]

## Contradictions
[Genuine tensions within the tradition — present both sides]

RULES:
- Use actual verse references from the context provided
- Tag life situations from human experience, not abstract philosophy
- The teaching story should be vivid and narrative, not encyclopedic
- Sanskrit terms (dharma, karma, moksha etc.) use as-is with explanation"""

    else:
        prompt = f"""Create a KNOWLEDGE BLOCK for: "{spec.title}"
Description: {spec.description}

VERSE CONTEXT FROM SCRIPTURE:
{verse_context}

Write in this exact format:
---
title: {spec.title}
type: knowledge
tags: [relevant, tags, here]
---

## Summary
[Who/what this is, in 2-3 sentences]

## The Story
[Tell the FULL story as a narrative. What happened, who was involved, what choices were made, what the consequences were. Include dialogue where appropriate. 5-8 paragraphs. Make it vivid — this should be enjoyable to read, not an encyclopedia entry.]

## Characters
- [Character]: [role in this story]

## Significance
[Why this story matters — what values does it illustrate, what does it teach]

## Key Verses
- [verse:ref] — "[the most important shloka]"

## Connected Stories
- [what comes before/after in the narrative]

## Teachings Within
- [wisdom themes this story illustrates]

## Common Questions
- [questions people commonly ask about this]

RULES:
- Tell it as a story, not a summary
- Include emotional moments and dialogue
- Use actual verse references from the context provided
- Make it accessible to someone unfamiliar with Hindu texts"""

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
        # Check for thinking/reasoning models that put content elsewhere
        reasoning = data["choices"][0]["message"].get("reasoning_content", "")
        if reasoning:
            return reasoning
        return f"[Generation failed: finish_reason={finish}]"
    return content


def write_to_gbrain(slug: str, content: str, dry_run: bool = False) -> bool:
    """Write a block to GBrain via CLI."""
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


def embed_all() -> None:
    """Run gbrain embed --all."""
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


def get_existing_slugs() -> set[str]:
    """Get slugs already in GBrain."""
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
    parser = argparse.ArgumentParser(description="Batch build wisdom graph")
    parser.add_argument("--chromadb-dir", default=os.environ.get("VEDANTA_CHROMADB_DIR", "./knowledge/chromadb"))
    parser.add_argument("--model", default="moonshotai/kimi-k2.6")
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't write to GBrain")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip blocks already in GBrain")
    parser.add_argument("--type", choices=["wisdom", "knowledge", "all"], default="all")
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
    if args.type in ("wisdom", "all"):
        blocks.extend(WISDOM_BLOCKS)
    if args.type in ("knowledge", "all"):
        blocks.extend(KNOWLEDGE_BLOCKS)

    total = len(blocks)
    skipped = 0
    created = 0
    failed = 0

    # Filter to blocks that need processing
    to_process = []
    for spec in blocks:
        if spec.slug in existing:
            skipped += 1
        else:
            to_process.append(spec)

    print(f"Building {len(to_process)} blocks with {args.model} (skipping {skipped} existing)")
    print(f"ChromaDB: {chromadb_dir}")
    print()

    if not to_process:
        print("Nothing to do — all blocks exist.")
        return

    # Phase 1: fetch verse context for all blocks (fast, local ChromaDB)
    print("Phase 1: Fetching verse context...")
    verse_contexts: dict[str, str] = {}
    for spec in to_process:
        verse_contexts[spec.slug] = fetch_verses(client, spec.search_queries, spec.verse_refs)
    print(f"  {len(verse_contexts)} contexts fetched")

    # Phase 2: parallel LLM calls (the bottleneck)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print("\nPhase 2: Generating blocks (5 concurrent)...")
    generated: dict[str, str] = {}

    def _generate_one(spec: BlockSpec) -> tuple[str, str | None]:
        try:
            content = generate_block(spec, verse_contexts[spec.slug], api_key, args.model)
            return (spec.slug, content)
        except Exception as e:
            print(f"  ERROR {spec.slug}: {e}")
            return (spec.slug, None)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_generate_one, spec): spec for spec in to_process}
        for i, future in enumerate(as_completed(futures), 1):
            spec = futures[future]
            slug, content = future.result()
            if content:
                generated[slug] = content
                print(f"  [{i}/{len(to_process)}] {slug} — {len(content)} chars")
            else:
                failed += 1
                print(f"  [{i}/{len(to_process)}] {slug} — FAILED")

    # Phase 3: write to GBrain (sequential — subprocess calls)
    print(f"\nPhase 3: Writing {len(generated)} blocks to GBrain...")
    for slug, content in generated.items():
        if write_to_gbrain(slug, content, dry_run=args.dry_run):
            created += 1
        else:
            failed += 1

    print("\n=== Done ===")
    print(f"Created: {created}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")
    print(f"Total in GBrain: {len(existing) + created}")

    if created > 0 and not args.dry_run:
        embed_all()


if __name__ == "__main__":
    main()
