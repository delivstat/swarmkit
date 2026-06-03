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
# Major figures from Mahabharata, Ramayana, Puranas, and other texts.
# Each gets a GBrain page with identity, key stories, flaws/virtues,
# relationships, and key verses — so STUDY mode queries about
# characters hit GBrain directly instead of expensive ChromaDB scans.

CHARACTER_BLOCKS = [
    # -- Devas and cosmic figures --
    BlockSpec(
        slug="indra",
        block_type="character",
        title="Indra — King of the Devas",
        description="Indra: king of devas, lord of thunder and rain. Deeply flawed — lustful (Ahalya), insecure (fears ascetics), jealous (persecutes devotees). His position is functional (cosmic order) not moral. Key stories: Ahalya curse, Vritra killing, Nahusha replacement, Govardhan. Epithet Sahasraksha from punishment.",
        search_queries=["Indra king devas ego jealousy", "Indra Ahalya curse Gautama", "Nahusha becomes king gods Indra throne", "Indra Vritra Brahminicide"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="arjuna",
        block_type="character",
        title="Arjuna — The Conflicted Warrior",
        description="Arjuna: third Pandava, greatest archer, Krishna's friend and student. His crisis on the battlefield — unwilling to fight family — triggers the entire Bhagavad Gita. Represents every person facing moral paralysis. Key moments: Gita dialogue, Pashupatastra, Subhadra marriage, Abhimanyu's death.",
        search_queries=["Arjuna warrior crisis battlefield Gita", "Arjuna refuses fight family Kurukshetra", "Arjuna Pashupatastra Shiva"],
        verse_refs=["gita:1:28", "gita:2:7", "gita:11:9"],
    ),
    BlockSpec(
        slug="krishna",
        block_type="character",
        title="Krishna — Teacher, Friend, Divine",
        description="Krishna: cowherd, prince, diplomat, charioteer, guru, divine incarnation. Multifaceted — playful in Vrindavan, strategic in Kurukshetra, compassionate in the Gita. Teaches through relationship, not commandment. Key roles: Gita teacher, Mahabharata diplomat, Govardhan protector, Draupadi's protector.",
        search_queries=["Krishna teacher charioteer Gita divine", "Krishna Vrindavan childhood", "Krishna Draupadi protection", "Krishna universal form Vishwarupa"],
        verse_refs=["gita:4:7", "gita:4:8", "gita:11:32"],
    ),
    BlockSpec(
        slug="draupadi",
        block_type="character",
        title="Draupadi — Fire-Born Queen",
        description="Draupadi: born from sacrificial fire, wife of five Pandavas, queen humiliated in the assembly hall. Her disrobing is the moral turning point of the Mahabharata. Fierce, intelligent, outspoken. Challenges patriarchal silence. Key moments: svayamvara, disrobing, exile, vengeance demand.",
        search_queries=["Draupadi disrobing assembly hall humiliation", "Draupadi fire born five husbands", "Draupadi exile forest dignity"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="rama",
        block_type="character",
        title="Rama — The Ideal King",
        description="Rama: prince of Ayodhya, Vishnu avatar, the maryada purushottama (ideal man). Exiled 14 years, loses Sita to Ravana, builds army of monkeys, wins war. But also the complex figure who abandons pregnant Sita for public opinion. The tradition debates this endlessly.",
        search_queries=["Rama ideal king exile Ayodhya", "Rama Sita Lanka war", "Rama abandons Sita Uttara Kanda", "Rama maryada purushottama"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="sita",
        block_type="character",
        title="Sita — Strength Through Suffering",
        description="Sita: daughter of the earth, Rama's wife, abducted by Ravana. Her captivity in Lanka — refusing Ravana, maintaining dignity — is a study in inner strength. The agni pariksha (fire ordeal) and eventual return to the earth are among the most debated episodes in Hindu literature.",
        search_queries=["Sita abducted Ravana Lanka captivity", "Sita fire ordeal agni pariksha", "Sita earth swallows Uttara Kanda", "Sita Ashoka grove dignity"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="karna",
        block_type="character",
        title="Karna — The Tragic Hero",
        description="Karna: abandoned son of Kunti and Surya, raised by a charioteer. Greatest warrior on the wrong side. His tragedy: loyalty to Duryodhana who gave him dignity when everyone else rejected him. Generous to a fault — gives away his armor. Killed unfairly by Arjuna. The most sympathetic 'villain' in world literature.",
        search_queries=["Karna tragic hero charioteer son Kunti", "Karna Duryodhana loyalty friendship", "Karna armor kavach kundal generous", "Karna death Arjuna unfair"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="yudhishthira",
        block_type="character",
        title="Yudhishthira — The Dharma King",
        description="Yudhishthira: eldest Pandava, son of Dharma himself. Never lies — except once, and it costs him. Gambles away his kingdom, brothers, wife. Bears humiliation rather than abandon dharma. His dog at heaven's gate is one of literature's greatest moral tests.",
        search_queries=["Yudhishthira dharma truth never lies", "Yudhishthira dice game loss kingdom", "Yudhishthira dog heaven gate test"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="bhishma",
        block_type="character",
        title="Bhishma — The Terrible Vow",
        description="Bhishma: gave up his throne, his right to marry, his entire life — for his father's happiness. His vow of celibacy is so extreme that even the gods respect it. But his tragedy: bound by that vow, he stands silent while Draupadi is humiliated. Duty can become a prison.",
        search_queries=["Bhishma vow celibacy throne father", "Bhishma bed arrows death", "Bhishma silent Draupadi disrobing"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="hanuman",
        block_type="character",
        title="Hanuman — Devotion as Superpower",
        description="Hanuman: monkey god, son of Vayu, Rama's greatest devotee. His strength is literally proportional to his devotion. Leaps across the ocean, carries a mountain, burns Lanka. Yet the most powerful being in the Ramayana is also the most humble. Opens his chest to show Rama in his heart.",
        search_queries=["Hanuman devotion Rama strength monkey", "Hanuman leaps ocean Lanka mountain", "Hanuman burns Lanka fire tail", "Hanuman opens chest Rama heart"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="ravana",
        block_type="character",
        title="Ravana — The Learned Demon",
        description="Ravana: ten-headed king of Lanka, great scholar, Shiva devotee, veena player. Not a simple villain — he's the most learned being of his age. His downfall is desire (for Sita) and ego (refusing to return her). The tradition respects his scholarship while condemning his actions.",
        search_queries=["Ravana ten heads Lanka scholar Shiva", "Ravana Sita abduction ego desire", "Ravana veena devotee Shiva learned"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="duryodhana",
        block_type="character",
        title="Duryodhana — Jealousy Incarnate",
        description="Duryodhana: eldest Kaurava, consumed by jealousy of the Pandavas from childhood. Not unintelligent — he's a skilled ruler and a generous friend to Karna. But envy poisons everything. The dice game, the disrobing, the war — all spring from his inability to accept others' success.",
        search_queries=["Duryodhana jealousy Pandavas hatred", "Duryodhana dice game cheat", "Duryodhana Karna friendship generous", "Duryodhana thigh Draupadi insult"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vidura",
        block_type="character",
        title="Vidura — The Voice of Conscience",
        description="Vidura: half-brother to Dhritarashtra, born of a servant. The wisest counselor in Hastinapura, repeatedly warning Dhritarashtra against his sons' adharma. Always ignored. His niti (practical wisdom) is among the most quotable teachings in the Mahabharata.",
        search_queries=["Vidura wisdom counsel Dhritarashtra warning", "Vidura niti practical wisdom", "Vidura advises king servant son"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="shakuntala",
        block_type="character",
        title="Shakuntala — Love, Loss, and Identity",
        description="Shakuntala: raised in a forest hermitage, falls in love with King Dushyanta. He forgets her due to a sage's curse. She must prove her own identity to the man who loved her. Her story — told by Kalidasa and in the Mahabharata — is about being forgotten by those who should remember you.",
        search_queries=["Shakuntala Dushyanta love forgotten curse", "Shakuntala ring recognition Kalidasa"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="shiva",
        block_type="character",
        title="Shiva — The Destroyer Who Renews",
        description="Shiva: ascetic and householder, destroyer and protector, meditator and dancer. Lives on Mount Kailasa covered in ash, wearing snakes, the moon in his hair. Drinks poison to save the world (Neelkantha). His tandava dance dissolves and recreates the universe. The god of paradox.",
        search_queries=["Shiva destroyer renewer ascetic householder", "Shiva Neelkantha poison ocean churning", "Shiva tandava dance destruction", "Shiva Parvati Kailasa meditation"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vishnu",
        block_type="character",
        title="Vishnu — The Preserver",
        description="Vishnu: the sustainer of cosmic order, who incarnates whenever dharma declines. His dashavatara (ten incarnations) span from fish to future warrior. As Rama he models duty, as Krishna he teaches freedom. Reclining on Shesha in the cosmic ocean, dreaming the universe into being.",
        search_queries=["Vishnu preserver incarnation dashavatara", "Vishnu Rama Krishna avatar dharma", "Vishnu Shesha cosmic ocean Vaikuntha"],
        verse_refs=["gita:4:7", "gita:4:8"],
    ),
    # -- Mahabharata characters (beyond the existing ones) --
    BlockSpec(
        slug="gandhari",
        block_type="character",
        title="Gandhari — The Blindfolded Queen",
        description="Gandhari: princess of Gandhara, wife of blind Dhritarashtra. Blindfolded herself for life to share her husband's world. Mother of 100 Kauravas. Her curse on Krishna after the war — that his clan would destroy itself — came true. Embodies the tragedy of misplaced loyalty and the power of a mother's grief.",
        search_queries=["Gandhari blindfold husband Dhritarashtra", "Gandhari curse Krishna Yadava destruction", "Gandhari hundred sons Kauravas mother"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="kunti",
        block_type="character",
        title="Kunti — Mother of Secrets",
        description="Kunti: mother of three Pandavas (via divine boons) and secretly of Karna (via Surya before marriage). Her greatest burden: knowing Karna is her son while he fights against her other sons. She asks Krishna to keep the secret. Embodies the impossible choices mothers face.",
        search_queries=["Kunti mother Pandavas Karna secret", "Kunti Surya son abandoned river", "Kunti divine boon children gods"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="dhritarashtra",
        block_type="character",
        title="Dhritarashtra — The Blind King",
        description="Dhritarashtra: blind from birth, king by default, father of Duryodhana. His blindness is both literal and moral — he sees his sons' adharma but refuses to act. Vidura warns him repeatedly; he listens and does nothing. The archetype of willful ignorance in positions of power.",
        search_queries=["Dhritarashtra blind king Vidura counsel ignored", "Dhritarashtra sons adharma weakness", "Dhritarashtra Sanjaya war narration"],
        verse_refs=["gita:1:1"],
    ),
    BlockSpec(
        slug="drona",
        block_type="character",
        title="Drona — The Compromised Teacher",
        description="Drona: greatest teacher of arms, guru to both Pandavas and Kauravas. Demanded Ekalavya's thumb as guru dakshina to protect Arjuna's supremacy. Fought for Kauravas despite knowing they were wrong — bound by salary, not dharma. His death required a lie: Yudhishthira said 'Ashwatthama is dead' (meaning the elephant).",
        search_queries=["Drona teacher Arjuna Ekalavya thumb guru dakshina", "Drona death Ashwatthama elephant lie Yudhishthira", "Drona fights Kauravas wrong side"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="ashwatthama",
        block_type="character",
        title="Ashwatthama — Revenge and Its Cost",
        description="Ashwatthama: son of Drona. After his father's death by deception, he massacres the sleeping Pandava camp at night, killing Draupadi's five sons. Fires the Brahmastra at the unborn Parikshit. Cursed by Krishna to wander immortal, diseased, in agony for 3000 years. The ultimate cautionary tale about revenge.",
        search_queries=["Ashwatthama night massacre sleeping sons", "Ashwatthama curse Krishna immortal wandering", "Ashwatthama Brahmastra Parikshit unborn"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="shakuni",
        block_type="character",
        title="Shakuni — The Master Manipulator",
        description="Shakuni: uncle of Duryodhana, master of dice. His loaded dice won the gambling match that exiled the Pandavas and humiliated Draupadi. His motivation: revenge against the Kuru house for the death of his family. Shakuni didn't just cheat — he engineered the entire war. The puppeteer who destroyed a dynasty.",
        search_queries=["Shakuni dice game cheat loaded", "Shakuni uncle Duryodhana manipulation revenge", "Shakuni Gandhara family destroyed Kuru"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="ekalavya",
        block_type="character",
        title="Ekalavya — Devotion and Injustice",
        description="Ekalavya: tribal boy who wanted to learn archery from Drona but was rejected for his caste. Built a clay statue of Drona and taught himself, becoming a better archer than Arjuna. When Drona discovered this, he demanded Ekalavya's right thumb as guru dakshina. Ekalavya cut it off without hesitation. The most devastating critique of caste discrimination in the Mahabharata.",
        search_queries=["Ekalavya thumb guru dakshina Drona rejected", "Ekalavya self-taught archer tribal caste", "Ekalavya clay statue devotion sacrifice"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="abhimanyu",
        block_type="character",
        title="Abhimanyu — The Boy Who Entered But Could Not Exit",
        description="Abhimanyu: son of Arjuna and Subhadra, 16 years old. Learned to enter the chakravyuha formation in the womb but never learned how to exit. On the 13th day of war, he entered alone, fought brilliantly, and was killed by six warriors who broke all rules of combat. His death broke Arjuna.",
        search_queries=["Abhimanyu chakravyuha formation entered trapped", "Abhimanyu death young warrior unfair", "Abhimanyu womb learned Arjuna Subhadra"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="ghatotkacha",
        block_type="character",
        title="Ghatotkacha — The Demon Son's Sacrifice",
        description="Ghatotkacha: half-demon son of Bhima and Hidimbi. His demonic powers grew at night. On the 14th night of war, he devastated the Kaurava army. Karna used the Shakti (Indra's weapon, meant for Arjuna) to kill him. Krishna celebrated — because the weapon meant for Arjuna was now spent. Ghatotkacha's death saved Arjuna's life.",
        search_queries=["Ghatotkacha demon son Bhima night battle", "Ghatotkacha Karna Shakti weapon Indra", "Ghatotkacha death saves Arjuna"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="amba-shikhandi",
        block_type="character",
        title="Amba/Shikhandi — Revenge Across Lifetimes",
        description="Amba: princess abducted by Bhishma for his brother. When Bhishma refused to marry her or return her, she swore revenge. Performed severe penance, died, and was reborn as Shikhandi — a woman who became a man. In the war, Shikhandi stood before Bhishma, who refused to fight a woman. Arjuna shot from behind Shikhandi. Revenge took an entire lifetime.",
        search_queries=["Amba Bhishma abducted revenge rebirth", "Shikhandi woman man Bhishma war shield", "Amba penance Shiva rebirth Drupada"],
        verse_refs=[],
    ),
    # -- Ramayana characters --
    BlockSpec(
        slug="lakshmana",
        block_type="character",
        title="Lakshmana — The Devoted Brother",
        description="Lakshmana: Rama's younger brother, who chose 14 years of exile over comfort. Drew the Lakshman Rekha to protect Sita. Cut off Surpanakha's nose. Nearly died to Indrajit's Shakti but was saved by Sanjeevani herb. The ideal of fraternal devotion — his life existed entirely in service to Rama.",
        search_queries=["Lakshmana brother Rama exile devoted", "Lakshmana Rekha line Sita protection", "Lakshmana Indrajit Shakti Sanjeevani herb"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="bharata",
        block_type="character",
        title="Bharata — The King Who Served Sandals",
        description="Bharata: son of Kaikeyi, could have claimed Rama's throne. Instead, he placed Rama's sandals on the throne and ruled as regent for 14 years, living like an ascetic. When Rama returned, he ran barefoot to meet him. The purest example of dharma over self-interest in the Ramayana.",
        search_queries=["Bharata sandals throne Rama regent", "Bharata refuses crown Rama exile", "Bharata Nandigram ascetic rule"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="sugriva",
        block_type="character",
        title="Sugriva — The Exiled Monkey King",
        description="Sugriva: monkey prince, exiled by his brother Vali. Allied with Rama — Sugriva gave his army, Rama killed Vali. But Sugriva forgot his promise when comfortable again. Lakshmana had to threaten him. A portrait of how power corrupts even the wronged, and how alliances require accountability.",
        search_queries=["Sugriva monkey king Vali exile Rama alliance", "Sugriva forgets promise Lakshmana threatens", "Sugriva Rama Vali killed alliance army"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vibhishana",
        block_type="character",
        title="Vibhishana — Dharma Over Blood",
        description="Vibhishana: Ravana's brother who defected to Rama. Tried to counsel Ravana to return Sita; was mocked and exiled. Joined Rama and revealed Lanka's secrets. Called a traitor by some traditions, a dharma-follower by others. The eternal question: when does loyalty to family become complicity in their crimes?",
        search_queries=["Vibhishana brother Ravana defects Rama dharma", "Vibhishana counsel Ravana return Sita rejected", "Vibhishana traitor dharma debate"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="kaikeyi",
        block_type="character",
        title="Kaikeyi — The Queen Who Changed Everything",
        description="Kaikeyi: Dasharatha's favorite wife, Bharata's mother. Once saved Dasharatha's life in battle (he gave her two boons). Manipulated by Manthara, she used those boons to exile Rama and crown Bharata. Her action triggers the entire Ramayana. Regretted it for the rest of her life. Ambition poisoned by bad counsel.",
        search_queries=["Kaikeyi two boons Rama exile Bharata crown", "Kaikeyi Manthara manipulation queen", "Kaikeyi Dasharatha saved battle boons"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="dasharatha",
        block_type="character",
        title="Dasharatha — Death by Broken Heart",
        description="Dasharatha: king of Ayodhya, Rama's father. Bound by his promise to Kaikeyi, he exiles his beloved son. Dies of grief shortly after. Earlier in life, he accidentally killed Shravan Kumar (a boy carrying his blind parents) and was cursed by the parents: 'You too will die grieving for your son.' The curse fulfilled.",
        search_queries=["Dasharatha death grief Rama exile", "Dasharatha Shravan Kumar accidental killing curse", "Dasharatha Kaikeyi promise bound exile son"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="jatayu",
        block_type="character",
        title="Jatayu — The Eagle Who Fought Alone",
        description="Jatayu: aged eagle king, friend of Dasharatha. When Ravana abducted Sita, Jatayu alone tried to stop him. Old, outmatched, he fought until Ravana cut his wings. Dying, he told Rama which direction Ravana went. Rama performed his last rites as a son would for a father. Courage is not about winning — it's about trying.",
        search_queries=["Jatayu eagle fights Ravana Sita abduction", "Jatayu dying tells Rama direction", "Jatayu Rama last rites father"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="mandodari",
        block_type="character",
        title="Mandodari — The Wise Wife Ignored",
        description="Mandodari: Ravana's wife, one of the panchakanya (five ideal women). Repeatedly begged Ravana to return Sita. She saw the destruction coming. He ignored her — like Gandhari and Dhritarashtra in the Mahabharata. Her grief after Ravana's death is one of the most poignant laments in the Ramayana.",
        search_queries=["Mandodari wife Ravana counsel return Sita", "Mandodari grief lament Ravana death", "Mandodari panchakanya wise wife"],
        verse_refs=[],
    ),
    # -- Sages and teachers --
    BlockSpec(
        slug="narada",
        block_type="character",
        title="Narada — The Cosmic Storyteller",
        description="Narada: divine sage who travels between worlds carrying his veena and singing 'Narayana Narayana.' Part messenger, part troublemaker, part teacher. His 'interference' often catalyzes important events. Gave the Bhakti Sutras. Appears in virtually every Purana. The original disruptor.",
        search_queries=["Narada sage travels worlds veena", "Narada Bhakti Sutras devotion", "Narada stories troublemaker divine messenger"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vyasa",
        block_type="character",
        title="Vyasa — Author of Everything",
        description="Vyasa (Krishna Dvaipayana): compiled the Vedas, wrote the Mahabharata (including the Gita), authored the Puranas, and composed the Brahma Sutras. Born of Satyavati and Parashara on a river island. Father of Dhritarashtra, Pandu, and Vidura. The single most important figure in Hindu literary tradition. His name means 'compiler.'",
        search_queries=["Vyasa author Mahabharata Vedas compiler", "Vyasa Krishna Dvaipayana Satyavati Parashara", "Vyasa father Dhritarashtra Pandu Vidura"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vishwamitra",
        block_type="character",
        title="Vishwamitra — King Who Became a Sage",
        description="Vishwamitra: born a kshatriya king, performed such extreme penance that he became a brahmarishi. His rivalry with Vasistha is legendary. Created a parallel heaven. Sent Menaka to distract him — she succeeded, and Shakuntala was born. His story proves that caste is not destiny — transformation is possible through effort.",
        search_queries=["Vishwamitra king became sage brahmarishi", "Vishwamitra Vasistha rivalry penance", "Vishwamitra Menaka apsara Shakuntala born"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="agastya",
        block_type="character",
        title="Agastya — The Sage Who Drank the Ocean",
        description="Agastya: tiny in stature, immense in power. Drank the ocean to expose hiding demons. Cursed Nahusha into a serpent. Crossed the Vindhya mountains (which bowed and never rose again). Brought Vedic culture to South India. One of the Saptarishi. His power came from tapas, not size.",
        search_queries=["Agastya drank ocean demons sage", "Agastya Nahusha curse serpent", "Agastya Vindhya mountains south India"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="durvasa",
        block_type="character",
        title="Durvasa — The Angry Sage",
        description="Durvasa: famous for his terrible temper and devastating curses. Cursed Shakuntala to be forgotten by Dushyanta. Cursed Indra, leading to the churning of the ocean. But his anger also catalyzes transformation — without Durvasa's curse, the Samudra Manthan and the discovery of amrit would never have happened.",
        search_queries=["Durvasa angry sage curse Shakuntala", "Durvasa curse Indra ocean churning", "Durvasa temper sage transformation"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="parashurama",
        block_type="character",
        title="Parashurama — The Warrior-Priest",
        description="Parashurama: Vishnu's sixth avatar, a brahmin who took up the axe. Destroyed the kshatriya class 21 times to avenge his father Jamadagni's murder. Teacher of Bhishma, Drona, and Karna. Embodies the paradox of the violence needed to establish peace. His rage against injustice was both righteous and excessive.",
        search_queries=["Parashurama axe warrior brahmin avatar", "Parashurama 21 times kshatriya destruction", "Parashurama teacher Bhishma Drona Karna"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="chanakya",
        block_type="character",
        title="Chanakya — The Ruthless Strategist",
        description="Chanakya (Kautilya/Vishnugupta): political genius who overthrew the Nanda dynasty and installed Chandragupta Maurya. Author of the Arthashastra. Pragmatic to the point of ruthlessness. His maxims on governance, friendship, and power are still quoted. The Machiavelli of India — but 1,800 years earlier.",
        search_queries=["Chanakya Kautilya Arthashastra strategist", "Chanakya Chandragupta Nanda overthrow", "Chanakya niti wisdom governance power"],
        verse_refs=[],
    ),
    # -- Puranic figures --
    BlockSpec(
        slug="ganesha",
        block_type="character",
        title="Ganesha — Remover of Obstacles",
        description="Ganesha: elephant-headed god, son of Shiva and Parvati. Created by Parvati from turmeric paste, beheaded by Shiva who didn't recognize him, restored with an elephant's head. Worshipped before all beginnings. Wrote the Mahabharata as Vyasa dictated. His broken tusk became the pen. Wisdom, intellect, new beginnings.",
        search_queries=["Ganesha elephant head Shiva Parvati created", "Ganesha remover obstacles worship beginnings", "Ganesha wrote Mahabharata Vyasa dictated tusk"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="kartikeya",
        block_type="character",
        title="Kartikeya/Murugan — The Divine Commander",
        description="Kartikeya (Murugan/Skanda): god of war, son of Shiva, commander of the divine armies. Six-headed, born to destroy the demon Tarakasura. Immensely popular in South India as Murugan. His story involves the rivalry with Ganesha over who would marry first — Kartikeya circled the world, Ganesha circled his parents ('you are my world').",
        search_queries=["Kartikeya Murugan Skanda war god", "Kartikeya Tarakasura demon commander", "Kartikeya Ganesha rivalry race world parents"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="garuda",
        block_type="character",
        title="Garuda — The Divine Eagle",
        description="Garuda: king of birds, Vishnu's mount (vahana). Son of Vinata, born from an egg. Freed his mother from slavery to the nagas by stealing amrit from the gods — defeating Indra, the devas, and a ring of fire. His story is about filial devotion and courage against impossible odds.",
        search_queries=["Garuda eagle Vishnu mount bird king", "Garuda mother Vinata slavery nagas amrit", "Garuda steals amrit defeats Indra"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="prahlada",
        block_type="character",
        title="Prahlada — The Devotee King's Son",
        description="Prahlada: son of demon king Hiranyakashipu, devoted to Vishnu despite his father's hatred of the god. Survived every attempt on his life — thrown from cliffs, fed to snakes, burned in fire (Holika). Vishnu appeared as Narasimha to save him. His faith is absolute and unshakeable. The Bhagavata Purana's most powerful devotee.",
        search_queries=["Prahlada devotion Vishnu Hiranyakashipu father", "Prahlada Narasimha saved half-man lion", "Prahlada Holika fire survived devotee"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="narasimha",
        block_type="character",
        title="Narasimha — The God of Loopholes",
        description="Narasimha: Vishnu's fourth avatar, half-man half-lion. Created to kill Hiranyakashipu who had a boon of near-invulnerability (not killed by man or beast, not indoors or outdoors, not by day or night). Narasimha killed him at twilight, on a threshold, on his lap, with his claws. The divine finds a way through every loophole.",
        search_queries=["Narasimha half man lion Vishnu avatar", "Narasimha Hiranyakashipu boon loophole twilight", "Narasimha threshold neither indoor outdoor"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="saraswati",
        block_type="character",
        title="Saraswati — Goddess of Knowledge",
        description="Saraswati: goddess of learning, music, arts, and speech. Depicted with a veena, seated on a white lotus, wearing white (purity of knowledge). Consort of Brahma. The Saraswati river in the Vedas. Worshipped during Vasant Panchami. Knowledge as the highest form of wealth — it cannot be stolen, divided, or diminished by sharing.",
        search_queries=["Saraswati goddess knowledge learning music", "Saraswati veena white lotus Brahma", "Saraswati river Vedas worship knowledge"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="lakshmi",
        block_type="character",
        title="Lakshmi — Goddess of Prosperity",
        description="Lakshmi: goddess of wealth, fortune, and beauty. Born from the churning of the ocean (Samudra Manthan). Consort of Vishnu, accompanies him in every avatar (Sita with Rama, Rukmini with Krishna). Worshipped during Diwali. The texts emphasize that Lakshmi is fickle — she stays where there is dharma, cleanliness, and hard work, and leaves where there is adharma and sloth.",
        search_queries=["Lakshmi goddess wealth prosperity fortune", "Lakshmi Samudra Manthan ocean churning born", "Lakshmi Diwali worship stays dharma leaves"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="savitri",
        block_type="character",
        title="Savitri — The Woman Who Defeated Death",
        description="Savitri: married Satyavan knowing he would die in one year. When Yama came for his soul, she followed Death himself, debating dharma, winning boons through wit — without ever asking for her husband's life directly, until she trapped Yama in his own logic. She won Satyavan back from death. The ultimate story of devotion + intelligence.",
        search_queries=["Savitri Satyavan death Yama followed debated", "Savitri defeats Death logic boons", "Savitri devotion intelligence Yama trapped"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="damayanti",
        block_type="character",
        title="Damayanti — Love Tested by Fate",
        description="Damayanti: princess who chose King Nala over the gods themselves at her swayamvara (the gods disguised themselves as Nala, but she identified the real one). After Nala lost his kingdom to dice (like Yudhishthira), she endured exile and separation, eventually reuniting them through cleverness. A parallel to the Pandava story but with a woman as the hero.",
        search_queries=["Damayanti Nala swayamvara chose gods disguised", "Nala Damayanti dice loss exile reunion", "Damayanti swan love message Nala"],
        verse_refs=[],
    ),
]


CONCEPT_BLOCKS = [
    BlockSpec(
        slug="dharma",
        block_type="concept",
        title="Dharma — Duty, Order, Righteousness",
        description="Dharma: the most untranslatable word in Hindu philosophy. Cosmic order, personal duty, moral law, righteousness — all at once. Contextual: a warrior's dharma differs from a teacher's. The Mahabharata's central question: what IS dharma when duties conflict?",
        search_queries=["dharma duty righteousness cosmic order", "dharma conflict Mahabharata", "svadharma own duty Gita"],
        verse_refs=["gita:3:35", "gita:18:47"],
    ),
    BlockSpec(
        slug="karma",
        block_type="concept",
        title="Karma — Action and Consequence",
        description="Karma: not fate, not punishment — consequence. Every action creates results. The Gita's revolution: act without attachment to results (nishkama karma). Karma is not 'what happens to you' but 'what you set in motion.' Three types: sanchita (accumulated), prarabdha (current), kriyamana (being created now).",
        search_queries=["karma action consequence nishkama", "karma three types sanchita prarabdha", "karma yoga detached action Gita"],
        verse_refs=["gita:2:47", "gita:3:5", "gita:4:18"],
    ),
    BlockSpec(
        slug="moksha",
        block_type="concept",
        title="Moksha — Liberation",
        description="Moksha: freedom from the cycle of birth and death (samsara). The ultimate goal in Hindu philosophy. Three paths: jnana (knowledge), bhakti (devotion), karma (action). Different darshanas disagree on what moksha IS: merger with Brahman (Advaita), eternal presence with God (Vishishtadvaita), or distinct liberation (Dvaita).",
        search_queries=["moksha liberation samsara freedom cycle", "moksha jnana bhakti karma paths", "moksha Advaita Vishishtadvaita Dvaita differences"],
        verse_refs=["gita:4:35", "gita:5:28", "gita:18:66"],
    ),
    BlockSpec(
        slug="maya",
        block_type="concept",
        title="Maya — Illusion and the Nature of Reality",
        description="Maya: the power that makes the unreal appear real. Not 'the world is fake' — rather, the world is real but not ultimate. Shankara: maya is neither real nor unreal (anirvachaniya). The snake-rope analogy: you see a snake (world), but it's a rope (Brahman). Fear is real until you see the truth.",
        search_queries=["maya illusion reality Shankara", "maya snake rope analogy Brahman", "maya anirvachaniya neither real unreal"],
        verse_refs=["gita:7:14", "gita:7:15"],
    ),
    BlockSpec(
        slug="atman-brahman",
        block_type="concept",
        title="Atman and Brahman — Self and Absolute",
        description="Atman: the individual self, not the body or mind. Brahman: the ultimate reality, the ground of all being. The Upanishadic revelation: atman IS Brahman (tat tvam asi). You are not separate from the divine — you ARE the divine, temporarily forgetting. The entire Vedantic project is remembering this.",
        search_queries=["atman brahman self absolute identity", "tat tvam asi you are that Upanishad", "atman brahman Chandogya Mandukya"],
        verse_refs=["gita:2:20", "gita:13:2", "gita:15:7"],
    ),
    BlockSpec(
        slug="samsara",
        block_type="concept",
        title="Samsara — The Cycle of Birth and Death",
        description="Samsara: the wheel of birth, death, and rebirth driven by karma and desire. Not punishment — consequence of unresolved attachments. The soul (atman) transmigrates through bodies until liberation (moksha). The Gita: 'as a person casts off worn-out garments and puts on new ones, so the soul casts off bodies.'",
        search_queries=["samsara cycle birth death rebirth", "transmigration soul body garment Gita", "samsara wheel karma desire liberation"],
        verse_refs=["gita:2:22", "gita:2:13", "gita:8:15"],
    ),
    BlockSpec(
        slug="yoga",
        block_type="concept",
        title="Yoga — Union, Discipline, Path",
        description="Yoga: not just postures — union of individual consciousness with the universal. The Gita defines yoga as 'skill in action' (yogah karmasu kaushalam) and 'equanimity' (samatvam yoga uchyate). Four classical paths: karma (action), jnana (knowledge), bhakti (devotion), raja (meditation). All lead to the same summit.",
        search_queries=["yoga union discipline paths four", "karma jnana bhakti raja yoga", "yoga skill action equanimity Gita"],
        verse_refs=["gita:2:48", "gita:2:50", "gita:6:23"],
    ),
    BlockSpec(
        slug="ahimsa",
        block_type="concept",
        title="Ahimsa — Non-Violence",
        description="Ahimsa: non-violence in thought, word, and deed. The highest dharma (ahimsa paramo dharma). But the Mahabharata adds complexity: when violence is needed to protect the innocent, is non-violence still dharma? Arjuna's dilemma. Gandhi's interpretation vs the Gita's battlefield context.",
        search_queries=["ahimsa non-violence highest dharma", "ahimsa paramo dharma Mahabharata", "violence protection innocent dharma conflict"],
        verse_refs=["gita:16:2", "gita:2:31"],
    ),
    BlockSpec(
        slug="gunas",
        block_type="concept",
        title="Three Gunas — Sattva, Rajas, Tamas",
        description="Gunas: three fundamental qualities that constitute all of nature (prakriti). Sattva (clarity, harmony), rajas (passion, activity), tamas (inertia, darkness). Everything — food, action, knowledge, faith — has a guna profile. Liberation is transcending all three, not just choosing sattva.",
        search_queries=["gunas sattva rajas tamas three qualities", "gunas prakriti nature constitution", "transcend gunas liberation Gita"],
        verse_refs=["gita:14:5", "gita:14:10", "gita:14:25"],
    ),
    BlockSpec(
        slug="varna-jati",
        block_type="concept",
        title="Varna and Jati — Quality vs Birth",
        description="Varna: the four-fold classification (brahmana, kshatriya, vaishya, shudra). The Gita says it's based on guna and karma (quality and action), NOT birth. Jati: the hereditary caste system that developed later. Critical distinction: the texts describe varna as functional classification, not as birth-based hierarchy. Modern caste discrimination has no scriptural basis in the Gita's framework.",
        search_queries=["varna caste guna karma birth Gita", "catur varnyam maya srishtam guna karma", "caste discrimination varna jati difference"],
        verse_refs=["gita:4:13", "gita:18:41"],
    ),
    BlockSpec(
        slug="deva-asura",
        block_type="concept",
        title="Devas and Asuras — Gods and Demons",
        description="Devas: celestial beings who maintain cosmic order (rain, seasons, natural law). NOT morally perfect — Indra's flaws, Agni's desires. Asuras: powerful beings who oppose cosmic order, often through ego and desire for power. The distinction is functional, not moral. The Gita lists divine (daivi) and demonic (asuri) qualities that exist in every human.",
        search_queries=["deva asura gods demons cosmic order", "divine demonic qualities Gita daivi asuri", "devas imperfect Indra flawed cosmic function"],
        verse_refs=["gita:16:1", "gita:16:4", "gita:16:6"],
    ),
    BlockSpec(
        slug="bhakti",
        block_type="concept",
        title="Bhakti — The Path of Devotion",
        description="Bhakti: loving devotion to God as a path to liberation. Democratized spirituality — no caste, no learning, no austerity required. Just love. Nine forms (navavidha bhakti): listening, singing, remembering, serving, worship, prostration, servitude, friendship, self-surrender. The Alvars, Nayanmars, and Bhakti movement transformed Hinduism.",
        search_queries=["bhakti devotion love God liberation", "navavidha bhakti nine forms", "bhakti movement Alvars Nayanmars"],
        verse_refs=["gita:9:26", "gita:9:34", "gita:12:6"],
    ),
    BlockSpec(
        slug="tapas",
        block_type="concept",
        title="Tapas — Austerity and Inner Fire",
        description="Tapas: literally 'heat' — the burning intensity of focused spiritual practice. Not self-torture but disciplined effort. Parvati's tapas wins Shiva. Dhruva's tapas moves Vishnu. The Gita classifies tapas of body, speech, and mind. True tapas is consistent practice, not dramatic renunciation.",
        search_queries=["tapas austerity penance heat spiritual", "tapas body speech mind Gita three types", "Parvati tapas Shiva Dhruva tapas Vishnu"],
        verse_refs=["gita:17:14", "gita:17:15", "gita:17:16"],
    ),
    BlockSpec(
        slug="purushartha",
        block_type="concept",
        title="Purushartha — Four Goals of Human Life",
        description="Purushartha: the four aims — dharma (righteousness), artha (prosperity), kama (pleasure), moksha (liberation). Not hierarchical — all four are legitimate. The balance between them is the art of a good life. Neglect any one and life becomes incomplete. Artha without dharma is corruption; kama without dharma is addiction.",
        search_queries=["purushartha four goals dharma artha kama moksha", "four aims human life balance", "artha wealth kama pleasure dharma righteousness"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="ashrama",
        block_type="concept",
        title="Ashrama — Four Stages of Life",
        description="Ashrama: brahmacharya (student), grihastha (householder), vanaprastha (retirement), sannyasa (renunciation). Each stage has its own dharma. The householder stage is the foundation — it supports all others. The texts don't demand everyone become a sannyasi; they demand you live each stage fully.",
        search_queries=["ashrama four stages brahmacharya grihastha vanaprastha sannyasa", "stages life student householder retirement renunciation", "grihastha householder supports all"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="avatara",
        block_type="concept",
        title="Avatara — Divine Incarnation",
        description="Avatara: Vishnu's descent into the world whenever dharma declines. Ten classical avatars (dashavatara): Matsya (fish), Kurma (tortoise), Varaha (boar), Narasimha (man-lion), Vamana (dwarf), Parashurama (warrior), Rama, Krishna, Buddha, Kalki (future). Each responds to a specific cosmic crisis. The divine adapts its form to the problem.",
        search_queries=["avatara incarnation Vishnu dashavatara ten", "avatara dharma decline Gita yada yada hi dharmasya", "Matsya Kurma Varaha Narasimha Vamana avatars"],
        verse_refs=["gita:4:7", "gita:4:8"],
    ),
    BlockSpec(
        slug="samskara",
        block_type="concept",
        title="Samskara — Rituals and Impressions",
        description="Samskara has two meanings: (1) sacraments/rituals marking life transitions (16 samskaras from conception to death), and (2) mental impressions/conditioning from past experiences and lives. The rituals purify and mark transitions; the impressions shape personality and tendencies. Yoga aims to dissolve limiting samskaras.",
        search_queries=["samskara rituals sixteen life transitions", "samskara mental impressions conditioning yoga", "sixteen samskaras birth death marriage"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="yajna",
        block_type="concept",
        title="Yajna — Sacrifice and Sacred Exchange",
        description="Yajna: sacrifice — from Vedic fire rituals to the Gita's expanded meaning of any selfless action. The Gita says: all action should be yajna (offering). The universe runs on reciprocity: gods give rain, humans give offerings, the cycle sustains all. Yajna is not transaction — it's participation in cosmic interdependence.",
        search_queries=["yajna sacrifice fire ritual offering", "yajna Gita selfless action offering karma", "Vedic sacrifice cosmic reciprocity gods rain"],
        verse_refs=["gita:3:9", "gita:3:10", "gita:4:24"],
    ),
    BlockSpec(
        slug="prana",
        block_type="concept",
        title="Prana — Life Force and Breath",
        description="Prana: the vital life force that animates all living beings. Five pranas (pancha prana): prana (inhalation), apana (exhalation), samana (digestion), udana (upward movement), vyana (circulation). Pranayama (breath control) is a key yoga practice. The Upanishads teach that prana is superior to the senses — when prana leaves, the body is dead.",
        search_queries=["prana life force breath vital energy", "pancha prana five types apana samana", "pranayama breath control yoga practice"],
        verse_refs=["gita:4:29"],
    ),
    BlockSpec(
        slug="dana",
        block_type="concept",
        title="Dana — The Practice of Giving",
        description="Dana: charity, generosity, giving. Three types in the Gita: sattvic (given without expectation, to the right person, at the right time), rajasic (given for reward or with reluctance), tamasic (given to the wrong person, disrespectfully, or at the wrong time). Karna is the supreme example — he gave away his armor knowing it would kill him.",
        search_queries=["dana charity giving generosity three types", "sattvic rajasic tamasic dana Gita", "Karna generous giving armor dana"],
        verse_refs=["gita:17:20", "gita:17:21", "gita:17:22"],
    ),
    BlockSpec(
        slug="viveka",
        block_type="concept",
        title="Viveka — Discrimination and Discernment",
        description="Viveka: the ability to distinguish the real from the unreal, the eternal from the temporary, the self from the not-self. Shankara's Vivekachudamani ('Crest-jewel of Discrimination') makes it the first prerequisite for liberation. Not intellectual cleverness — deep, intuitive seeing of what is truly valuable.",
        search_queries=["viveka discrimination discernment real unreal", "Vivekachudamani Shankara crest jewel", "viveka nitya anitya eternal temporary"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vairagya",
        block_type="concept",
        title="Vairagya — Dispassion Without Numbness",
        description="Vairagya: dispassion, non-attachment. Not suppression of feeling but freedom from compulsive wanting. The Yoga Sutras pair it with abhyasa (practice) — both are needed. Vairagya without practice is laziness; practice without vairagya is grasping. The Gita's sthitaprajna (person of steady wisdom) embodies mature vairagya.",
        search_queries=["vairagya dispassion detachment non-attachment", "vairagya abhyasa practice pair Yoga Sutras", "sthitaprajna steady wisdom Gita vairagya"],
        verse_refs=["gita:2:55", "gita:2:56"],
    ),
    BlockSpec(
        slug="shraddha",
        block_type="concept",
        title="Shraddha — Faith as Foundation",
        description="Shraddha: faith, trust, conviction. The Gita says faith shapes reality: 'a person is made of their shraddha' (yo yac-chraddha sa eva sa). Three types: sattvic faith (in the divine, in dharma), rajasic (in power, in results), tamasic (in superstition, in inertia). Faith is not belief despite evidence — it's trust that sustains action before results appear.",
        search_queries=["shraddha faith trust conviction Gita", "yo yac chraddha sa eva sa person made faith", "three types faith sattvic rajasic tamasic"],
        verse_refs=["gita:17:2", "gita:17:3"],
    ),
    BlockSpec(
        slug="arishadvarga",
        block_type="concept",
        title="Arishadvarga — Six Enemies Within",
        description="Arishadvarga: the six inner enemies — kama (lust/desire), krodha (anger), lobha (greed), moha (delusion), mada (pride), matsarya (envy). Every villain in the epics embodies one or more. Ravana = kama. Duryodhana = matsarya. Hiranyakashipu = mada. The real war in the Mahabharata is internal.",
        search_queries=["arishadvarga six enemies kama krodha lobha", "six enemies moha mada matsarya internal", "desire anger greed delusion pride envy"],
        verse_refs=["gita:3:37", "gita:3:43", "gita:16:21"],
    ),
    BlockSpec(
        slug="loka",
        block_type="concept",
        title="Loka — Worlds and Realms",
        description="Loka: the cosmological realms. Seven upper lokas (Bhuloka/earth to Satyaloka/Brahma's realm) and seven lower (Atala to Patala). Not physical places but states of consciousness and karmic consequence. The devas live in Svarga (heaven) but even that is temporary — when merit is exhausted, you fall back to earth. Only moksha is permanent.",
        search_queries=["loka worlds realms seven upper lower", "Svarga heaven temporary merit exhausted", "fourteen lokas cosmology Bhuloka Satyaloka"],
        verse_refs=["gita:9:21"],
    ),
    BlockSpec(
        slug="rasa",
        block_type="concept",
        title="Rasa — Aesthetic Emotion and Essence",
        description="Rasa: literally 'essence' or 'juice.' In aesthetics (Bharata's Natyashastra): the nine fundamental emotions that art evokes — shringara (love), hasya (laughter), karuna (compassion), raudra (fury), vira (heroism), bhayanaka (terror), bibhatsa (disgust), adbhuta (wonder), shanta (peace). In devotion: the rasa of divine love (madhurya rasa). In Vedanta: Brahman is rasa — the ultimate essence.",
        search_queries=["rasa nine emotions Natyashastra aesthetic", "nava rasa shringara hasya karuna", "Brahman rasa essence Upanishad joy"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="sannyasa",
        block_type="concept",
        title="Sannyasa — Renunciation",
        description="Sannyasa: the formal renunciation of worldly life — the fourth ashrama. But the Gita redefines it: true sannyasa is not abandoning action but abandoning attachment to results. External renunciation without internal freedom is theater. Krishna: 'one who neither hates nor desires is a perpetual sannyasi.' You can renounce in a palace.",
        search_queries=["sannyasa renunciation worldly life fourth ashrama", "sannyasa Gita renounce attachment not action", "true renunciation internal external Gita"],
        verse_refs=["gita:5:3", "gita:18:2", "gita:18:11"],
    ),
    BlockSpec(
        slug="seva",
        block_type="concept",
        title="Seva — Selfless Service",
        description="Seva: service without expectation of return. The Gita's karma yoga in practice. Serving others is serving the divine in them. Gandhi built an independence movement on it. The tradition distinguishes seva (selfless) from upkara (favor, which creates obligation). True seva liberates both the giver and the receiver.",
        search_queries=["seva selfless service karma yoga practice", "service divine others Gandhi seva", "seva upkara difference selfless obligation"],
        verse_refs=["gita:3:19", "gita:3:25"],
    ),
    BlockSpec(
        slug="mantra",
        block_type="concept",
        title="Mantra — Sacred Sound",
        description="Mantra: sacred syllables with transformative power. AUM is the primordial mantra — the Mandukya Upanishad's entire teaching is on its four components (A-U-M-silence = waking-dreaming-deep sleep-turiya). Gayatri mantra is the most universal Vedic prayer. Mantras work through repetition (japa) — not magic but focused attention that transforms the mind.",
        search_queries=["mantra sacred sound AUM primordial", "Gayatri mantra Vedic prayer universal", "japa repetition mantra meditation focus"],
        verse_refs=["gita:10:25"],
    ),
    BlockSpec(
        slug="pancha-bhuta",
        block_type="concept",
        title="Pancha Bhuta — Five Elements",
        description="Pancha Bhuta: the five elements that constitute all matter — prithvi (earth), jala (water), agni (fire), vayu (air), akasha (space/ether). The body is made of these five; at death, each returns to its source. The elements correspond to the five senses and five tanmatras (subtle elements). Understanding them is understanding the material world.",
        search_queries=["pancha bhuta five elements earth water fire", "five elements body senses tanmatras", "prithvi jala agni vayu akasha"],
        verse_refs=["gita:7:4"],
    ),
    BlockSpec(
        slug="samudra-manthan",
        block_type="concept",
        title="Samudra Manthan — Churning of the Ocean",
        description="Samudra Manthan: devas and asuras churn the cosmic ocean using Mount Mandara as the churning rod and Vasuki (serpent) as the rope. Fourteen treasures emerge: Lakshmi, amrit (nectar of immortality), halahala (deadly poison — drunk by Shiva), the moon, the divine wish-granting cow, the celestial tree, and more. The teaching: creation requires cooperation between opposing forces, and transformation produces both nectar and poison.",
        search_queries=["Samudra Manthan churning ocean devas asuras", "ocean churning fourteen treasures amrit", "halahala poison Shiva Neelkantha Vasuki Mandara"],
        verse_refs=[],
    ),
]


# ---- TEXT OVERVIEW BLOCKS ----

TEXT_BLOCKS = [
    BlockSpec(
        slug="bhagavad-gita",
        block_type="text-overview",
        title="Bhagavad Gita — Song of God",
        description="700 verses in 18 chapters. Krishna teaches Arjuna on the battlefield of Kurukshetra. Part of Mahabharata (Bhishma Parva). Covers: karma yoga, jnana yoga, bhakti yoga, the nature of self, the three gunas, the universal form, liberation. The most widely read Hindu text worldwide.",
        search_queries=["Bhagavad Gita overview Krishna Arjuna battlefield", "Gita chapters themes karma jnana bhakti"],
        verse_refs=["gita:1:1", "gita:2:47", "gita:4:7", "gita:11:32", "gita:18:66"],
    ),
    BlockSpec(
        slug="mahabharata",
        block_type="text-overview",
        title="Mahabharata — The Great Epic",
        description="100,000+ verses. The longest epic poem in world literature. Vyasa's masterwork. The Pandava-Kaurava war, but much more: Vidura Niti, Bhishma's teachings, Yaksha Prashna, Nala-Damayanti, Savitri-Satyavan. Contains the Bhagavad Gita. 'What is found here may be found elsewhere; what is not found here is found nowhere.'",
        search_queries=["Mahabharata overview epic Pandava Kaurava", "Mahabharata structure parvas contents", "Mahabharata what is found here found nowhere"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="ramayana",
        block_type="text-overview",
        title="Ramayana — The Journey of Rama",
        description="24,000 verses by Valmiki. Rama's exile, Sita's abduction, the war against Ravana. Seven kandas (books). Not just adventure — a study of ideal conduct (maryada), the cost of duty, and whether dharma demands too much. Two major versions: Valmiki (older, grittier) and Tulsidas (devotional, Hindi).",
        search_queries=["Ramayana overview Valmiki Rama exile Lanka", "Ramayana seven kandas structure", "Ramayana Valmiki Tulsidas versions"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="upanishads",
        block_type="text-overview",
        title="Upanishads — The Philosophical Core",
        description="108+ texts, 10-13 considered principal. End portion of the Vedas (Vedanta = 'end of the Vedas'). The philosophical foundation: atman, Brahman, maya, moksha. Key Upanishads: Isha (all is Brahman), Kena (who moves the mind?), Katha (Nachiketa and Death), Mandukya (AUM and four states), Chandogya (tat tvam asi), Brihadaranyaka (neti neti).",
        search_queries=["Upanishads overview principal philosophical", "Upanishads Isha Kena Katha Mandukya Chandogya", "Vedanta end Vedas atman Brahman"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="yoga-vasistha",
        block_type="text-overview",
        title="Yoga Vasistha — The Supreme Yoga",
        description="32,000 verses. Sage Vasistha teaches young Rama about the nature of reality, consciousness, and self-effort. Radical Advaita: the world is a dream, the mind creates reality, self-effort trumps destiny. Contains extraordinary nested stories (Queen Lila, the hundred Rudras). The most sophisticated philosophical text in Hindu literature.",
        search_queries=["Yoga Vasistha overview Vasistha Rama consciousness", "Yoga Vasistha self-effort destiny mind reality", "Yoga Vasistha Queen Lila dream world"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="arthashastra",
        block_type="text-overview",
        title="Arthashastra — The Science of Statecraft",
        description="Kautilya/Chanakya's treatise on governance, economics, military strategy, law. Pragmatic, not idealistic. Written for rulers who must govern in an imperfect world. Often compared to Machiavelli but more systematic. Covers: treasury, army, spies, diplomacy, law, agriculture, trade. Brutally honest about power.",
        search_queries=["Arthashastra Kautilya Chanakya statecraft governance", "Arthashastra economy military law diplomacy", "Chanakya Niti practical wisdom leadership"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="panchatantra",
        block_type="text-overview",
        title="Panchatantra — Five Books of Wisdom",
        description="Animal fables teaching practical wisdom. Five books: Mitra Bheda (loss of friends), Mitra Labha (gaining friends), Kakolukiyam (crows and owls — war), Labdhapranasam (loss of gains), Aparikshitakarakam (hasty action). Translated into 50+ languages — one of the most translated works in human history. Teaches through entertainment.",
        search_queries=["Panchatantra fables five books animals wisdom", "Panchatantra Vishnu Sharma stories practical"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="puranas",
        block_type="text-overview",
        title="Puranas — Ancient Stories",
        description="18 Mahapuranas + 18 Upapuranas. Cosmology, genealogies, dharma, pilgrimage, philosophy — embedded in narrative. Key Puranas: Vishnu Purana (Vishnu's incarnations), Bhagavata Purana (Krishna's life, Prahlada), Shiva Purana (Shiva's forms), Markandeya Purana (Devi Mahatmyam). The Puranas made Vedantic philosophy accessible to everyone.",
        search_queries=["Puranas overview eighteen cosmology narrative", "Vishnu Purana Bhagavata Shiva Purana Markandeya", "Puranas dharma stories genealogy"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vedas",
        block_type="text-overview",
        title="Vedas — The Eternal Knowledge",
        description="Four Vedas: Rig (hymns), Yajur (rituals), Sama (chants), Atharva (practical). The oldest sacred texts, considered apaurusheya (authorless, eternal). Each Veda has four parts: Samhitas (hymns), Brahmanas (rituals), Aranyakas (forest texts), Upanishads (philosophy). The foundation of all Hindu thought.",
        search_queries=["Vedas four Rig Yajur Sama Atharva overview", "Vedas Samhita Brahmana Aranyaka Upanishad structure", "Vedas apaurusheya eternal knowledge"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="tirukural",
        block_type="text-overview",
        title="Tirukural — The Universal Ethics",
        description="1,330 couplets by Thiruvalluvar in Tamil. Three books: virtue (aram), wealth (porul), love (inbam). No sectarian affiliation — claimed by Hindus, Jains, and secular humanists alike. Pure ethical philosophy. 'Straighter than a measuring rod' — direct, practical, no mythology needed.",
        search_queries=["Tirukural Thiruvalluvar Tamil ethics virtue", "Kural three books aram porul inbam"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="devi-mahatmyam",
        block_type="text-overview",
        title="Devi Mahatmyam — Glory of the Goddess",
        description="700 verses from the Markandeya Purana. The Divine Mother's battles against Mahishasura, Shumbha-Nishumbha, Raktabija. Not just mythology — each demon represents an internal obstacle (ego, attachment, multiplying thoughts). The Goddess combines all the gods' powers. Central text of Shakta tradition, recited during Navaratri.",
        search_queries=["Devi Mahatmyam Goddess battles demons Mahishasura", "Durga Saptashati Markandeya Purana divine feminine", "Navaratri Devi Mahatmyam chanting"],
        verse_refs=[],
    ),
    # -- Individual texts we have in ChromaDB --
    BlockSpec(
        slug="brahma-sutras",
        block_type="text-overview",
        title="Brahma Sutras — The Logical Foundation",
        description="Badarayana's systematic summary of Upanishadic philosophy in terse aphorisms (sutras). 555 sutras in 4 chapters. The most commented-upon text: Shankara, Ramanuja, and Madhva all wrote major commentaries, each deriving a different conclusion. The opening sutra — 'athato brahma jijnasa' (now therefore the inquiry into Brahman) — defines the entire Vedantic project.",
        search_queries=["Brahma Sutras Badarayana systematic Upanishadic", "Brahma Sutras commentary Shankara Ramanuja Madhva", "athato brahma jijnasa inquiry Brahman"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="ashtavakra-gita",
        block_type="text-overview",
        title="Ashtavakra Gita — Radical Non-Duality",
        description="Dialogue between the deformed sage Ashtavakra and King Janaka. The most uncompromising Advaita text: 'You are pure consciousness. The world is an illusion. You were never born and will never die. Why do you weep?' No devotion, no ritual, no gradual path — just direct recognition. 298 verses of pure awakening.",
        search_queries=["Ashtavakra Gita non-dual consciousness Janaka", "Ashtavakra radical Advaita pure awareness", "Ashtavakra liberation instant recognition"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="yoga-sutras",
        block_type="text-overview",
        title="Yoga Sutras of Patanjali — The Science of Mind",
        description="196 sutras in 4 chapters (padas). Defines yoga as 'chitta vritti nirodha' (cessation of mental fluctuations). Eight limbs (ashtanga): yama, niyama, asana, pranayama, pratyahara, dharana, dhyana, samadhi. Not about postures — about the systematic mastery of the mind. The most influential text on meditation practice.",
        search_queries=["Yoga Sutras Patanjali chitta vritti nirodha", "ashtanga eight limbs yama niyama asana", "Patanjali meditation samadhi practice"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vivekachudamani",
        block_type="text-overview",
        title="Vivekachudamani — Crest-Jewel of Discrimination",
        description="Attributed to Adi Shankara. 580 verses on the path from bondage to liberation through discrimination (viveka) between the real (Brahman) and unreal (world). A student approaches a guru; the guru systematically dismantles every false identification (I am the body, I am the mind, I am the ego) until only Atman remains.",
        search_queries=["Vivekachudamani Shankara discrimination liberation", "Vivekachudamani crest jewel Atman Brahman", "Vivekachudamani body mind ego identification"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="hitopadesha",
        block_type="text-overview",
        title="Hitopadesha — Friendly Counsel",
        description="Animal fables based on the Panchatantra, compiled by Narayana. Four books: gaining friends, separating friends, war, peace. More explicitly didactic than the Panchatantra — each story ends with a clear moral verse. Written to educate princes in statecraft and practical wisdom. Widely used in education for centuries.",
        search_queries=["Hitopadesha friendly counsel fables Narayana", "Hitopadesha four books friends war peace", "Hitopadesha animal stories practical wisdom"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="bhagavata-purana",
        block_type="text-overview",
        title="Bhagavata Purana — The Supreme Devotion Text",
        description="18,000 verses, 12 cantos. The most popular Purana. Canto 10 (Krishna's life in Vrindavan) is the heart — the butter thief, the gopis' love, the rasa lila, Govardhan. Also contains Prahlada's story (Canto 7), Dhruva (Canto 4), and the Uddhava Gita (Canto 11). The foundational text of the bhakti tradition.",
        search_queries=["Bhagavata Purana Krishna Vrindavan gopis", "Bhagavata Purana twelve cantos devotion", "Bhagavata Purana Prahlada Dhruva Uddhava"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vishnu-purana",
        block_type="text-overview",
        title="Vishnu Purana — Cosmology and Avatar",
        description="One of the oldest Puranas. Six parts: creation, earth's structure, royal dynasties, protection by Vishnu, future dissolution, liberation through devotion. Key stories: Krishna's childhood, Prahlada, the churning of the ocean. More systematic than other Puranas — reads like a theological textbook structured as narrative.",
        search_queries=["Vishnu Purana cosmology creation avatar", "Vishnu Purana six parts dynasties liberation", "Vishnu Purana Krishna childhood Prahlada"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="chanakya-niti",
        block_type="text-overview",
        title="Chanakya Niti — Practical Maxims",
        description="Collection of aphorisms attributed to Chanakya on governance, relationships, money, education, and survival. Not connected to the Arthashastra — these are popular wisdom sayings. Brutally practical: 'test a servant, a friend, a wife, and relatives in times of difficulty.' Widely quoted in Indian culture.",
        search_queries=["Chanakya Niti maxims practical wisdom", "Chanakya sayings governance money friends", "Chanakya test friend difficulty practical"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="narada-bhakti-sutras",
        block_type="text-overview",
        title="Narada Bhakti Sutras — The Nature of Divine Love",
        description="84 aphorisms by Narada on the nature, characteristics, and practice of bhakti (devotion). Defines bhakti as 'supreme love for God' and describes its stages, obstacles, and fruits. Pairs with Patanjali's Yoga Sutras as a systematic treatment — Patanjali for the mind, Narada for the heart.",
        search_queries=["Narada Bhakti Sutras divine love devotion", "Narada 84 sutras supreme love God", "bhakti stages obstacles practice Narada"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="dhammapada",
        block_type="text-overview",
        title="Dhammapada — Path of Dharma",
        description="423 verses attributed to the Buddha. Among the most accessible Buddhist texts. Core teaching: 'Mind is the forerunner of all actions. All deeds are led by mind, created by mind.' Twin verses, the Fool, the Wise, Anger, Hatred, the Self, the World, the Buddha. Cross-tradition relevance — the ethics overlap significantly with Hindu dharma. Included in our collection for comparative study.",
        search_queries=["Dhammapada Buddha path dharma mind", "Dhammapada twin verses fool wise anger", "Dhammapada mind forerunner all actions"],
        verse_refs=[],
    ),
    # -- Individual Upanishads worth separate entries --
    BlockSpec(
        slug="katha-upanishad",
        block_type="text-overview",
        title="Katha Upanishad — Nachiketa's Questions to Death",
        description="Young Nachiketa is sent to Yama (Death) by his father's hasty curse. He waits three days at Death's door. Yama offers worldly boons — wealth, power, long life. Nachiketa refuses them all and asks the ultimate question: 'What happens after death?' Yama's answers form one of the most profound Upanishadic teachings on the Self, the body-chariot analogy, and the path beyond death.",
        search_queries=["Katha Upanishad Nachiketa Death Yama", "Nachiketa three boons refuses worldly", "Katha chariot body analogy self atman"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="mandukya-upanishad",
        block_type="text-overview",
        title="Mandukya Upanishad — AUM and Four States",
        description="The shortest major Upanishad — just 12 verses. Teaches that the syllable AUM contains all of reality. A = waking state (vaishvanara), U = dreaming state (taijasa), M = deep sleep (prajna), silence = turiya (the fourth, pure consciousness). Gaudapada's Karika commentary makes it the foundation of Advaita Vedanta. 'The Mandukya alone is sufficient for liberation' — Muktikopanishad.",
        search_queries=["Mandukya Upanishad AUM four states consciousness", "waking dreaming deep sleep turiya", "Mandukya twelve verses Gaudapada Karika"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="chandogya-upanishad",
        block_type="text-overview",
        title="Chandogya Upanishad — That Art Thou",
        description="One of the oldest and longest Upanishads. Contains the mahavakya 'Tat tvam asi' (That art thou) — Uddalaka's teaching to his son Shvetaketu. Also: the story of Satyakama Jabala (truth transcends caste), the teaching of the five fires, and the famous 'in the beginning there was Being alone' creation account.",
        search_queries=["Chandogya Upanishad tat tvam asi Uddalaka", "Satyakama Jabala truth caste Chandogya", "Chandogya five fires creation Being alone"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="isha-upanishad",
        block_type="text-overview",
        title="Isha Upanishad — The Lord Dwells in All",
        description="18 verses — the shortest major Upanishad. Opens with: 'All this — whatever moves in the moving world — is pervaded by the Lord. Enjoy through renunciation. Do not covet.' Reconciles action and knowledge, enjoyment and renunciation. Gandhi called it his favorite Upanishad.",
        search_queries=["Isha Upanishad Lord pervades all renunciation", "Isha eighteen verses ishavasyam idam sarvam", "Isha action knowledge enjoy renounce"],
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
