#!/usr/bin/env python3
"""
Ingest operator-authored card insights into miru_card_insights (card_catalog.db).

Session: operator_authored_session_2026_03_24
Run from repo root: python tools/ingest_operator_card_insights_2026_03_24.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.miru_project_sync import (  # noqa: E402
    DEFAULT_PROJECT_DB_PATH,
    classify_insight_quality,
    connect_catalog_db,
    ensure_catalog_sync_schema,
)


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    column_name = column_definition.split()[0]
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


CONFIDENCE_TARGET = 0.95
INSIGHT_TYPE = "usage"
SOURCE_REF = "operator_knowledge"
SYNC_REASON = "operator_authored_session_2026_03_24"

# (card_id, insight_text) — exact text, no alterations
INSIGHT_ROWS: list[tuple[str, str]] = [
    ("OP12-061", 'The engine. Spend 1 DON!! to knock 2 off the cost of your next big Law — and if that Law ever gets threatened, Rosinante steps in and takes a Life card to hand instead of losing it. The whole deck is built around this protection. Once you get the hang of it, it clicks fast.'),
    ("P-093", "This one surprised a lot of people when it dropped. It's a solid Blocker that actually gives you a rested DON!! back when you enter it — as long as you're at equal or less DON!! than your opponent. In Rosinante that condition is almost always met, so you get to defend without really losing your momentum. Great pickup."),
    ("EB04-038", "This card was basically made for this deck. It counts as both Law and Rosinante, so it gets the cost reduction and the Leader protection. On top of that, when it enters you draw a card and get an active DON!! back. Big Blocker that keeps your engine running — exactly what the deck needed."),
    ("EB03-062", "This is the card that really pushed Rosinante into a different tier. It comes in swinging with Rush, and then you can activate its effect to trash it, add a Life card back, and drop another Law from your hand. So one card becomes an attack, some Life recovery, and a fresh threat. Once you see it go off for the first time it makes total sense."),
    ("OP12-073", "Your big midgame play. Eight thousand power for 7 cost, and when it enters it recovers a DON!! and pumps all your Rosinante and Heart Pirates characters by 1000 until your opponent's next turn. In a deck that intentionally runs low on DON!!, the ramp back is huge. This is usually what you're setting up toward."),
    ("OP12-115", "Don't sleep on this event. It's a 1-cost counter that gives plus 2000 power during battle, and if you're at 2 or less Life it also pulls a Trafalgar Law back from your trash to hand. The trick is using it as a counter first, then getting the Law back to use again later. A lot of newer players miss that — it's not just a counter card."),
    ("ST10-010", "Hand control in a midrange deck. Spend 1 DON!! and if your opponent has 7 or more cards in hand, you pick and trash 2 of them. Opponents holding a big hand usually have counters ready — this takes that away. Not something you need every game, but when it matters it really matters."),
    ("OP12-108", "Your early game setup piece. This card helps you dig for the Laws you need before the midgame kicks off. Rosinante works best when you already have strong Law targets in hand — this is how you make sure that happens consistently. Run it, find your pieces early, and the rest of the deck flows much more naturally."),
    ("OP09-069", "Another consistency piece for the early game. The deck lives and dies by having the right Laws ready when Rosinante's cost reduction is online. This card helps you get there. Not flashy, but the kind of card that makes everything else work — experienced Rosinante players know exactly why it's in the list."),
    ("OP13-079", "Imu is the kind of deck that looks like it's doing nothing for the first few turns — and then the whole board arrives at once. The secret is the trash. Build it right, get your Five Elders in the graveyard, and The Empty Throne lets you flood the field for a fraction of what it should cost. Intimidating at first, but once you understand the setup it's one of the most satisfying decks to pilot."),
    ("OP13-099", "This is the card that makes Imu unfair. It lets you play a Five Elders character for just 3 cost — up to the number of DON!! you have on field. Without this Stage online, the deck is just setup cards with no payoff. With it, one turn can put multiple massive bodies on board at the same time. Find it, protect it, win."),
    ("OP05-097", "Imu starts the game with this already in play — which means the deck has structural setup before turn one even happens. It's a small advantage but it sets the tone. Imu doesn't play from behind; it starts ahead and stays there."),
    ("OP13-086", "Your early game workhorse. She helps you find Five Elders pieces while also loading the trash — which is exactly what Imu needs in the first few turns. Once she's done her job you can cash her in with the Leader effect. Efficient, unassuming, and genuinely important to how smoothly the deck runs."),
    ("OP13-096", "One of the best setup Events in the deck. It builds your hand and fills your trash at the same time — the two things Imu cares about most in the early game. If you're new to the deck, getting familiar with this card's timing is one of the first things that will make your games feel much cleaner."),
    ("OP13-084", "Don't overlook this one. Ju Peter sets the base power of your Five Elders to 7000 — which turns a big board into a board that actually ends games. The difference between hitting for 5000 and hitting for 7000 across multiple bodies is massive. This is what makes the payoff turn lethal instead of just impressive."),
    ("OP13-082", "This is what you're building toward. With the right Elder spread in your trash and The Empty Throne active, this card creates a board swing that most decks simply can't answer in one turn. Everything else in the deck exists to make this moment happen as cleanly and as early as possible."),
    ("OP13-083", "A key piece of the Elder chain. Saturn's On Play effect extends your value and helps set up future Five Elders turns — so the deck doesn't just fire once and hope. He's part of what makes Imu feel like it can keep reloading pressure even if the first wave doesn't close things out."),
    ("OP13-089", "Another Elder that keeps giving after he's played. His On K.O. effect helps you find more Five Elders pieces, which means the opponent can't just remove him and move on. Taking him out can actually help Imu set up the next wave. Opponents learn this the hard way."),
    ("OP13-080", "Imu isn't just sitting there doing nothing while it sets up — Nusjuro is part of why. He handles opposing characters while the engine comes online, which buys the time the deck needs to reach its payoff turn safely."),
    ("OP13-091", "Board control while you're still assembling. Mars keeps opposing threats in check so the opponent can't run away with the game during your setup window. Imu needs that breathing room, and Mars is one of the cards that provides it."),
    ("OP13-098", "Core disruption Event. Imu isn't just a combo deck that ignores the opponent — this Event is part of how it stays in control while building toward the payoff turn. Flexible, impactful, and one of the cards that makes the shell feel complete rather than fragile."),
    ("OP14-096", "A modern addition that's helped Imu stay near the top of the OP14.5 format. High-quality interaction that the deck needed to compete in the current field. If you're seeing current Imu lists, this one is almost certainly in them."),
    ("OP11-097", "Utility and smoothness. Not the flashiest card in the deck but it helps keep things running cleanly between setup and payoff. Experienced Imu players know how much these kinds of glue Events matter when the deck needs to buy one more turn."),
    ("OP13-002", "Ace only has 3 Life, which sounds scary — but that's kind of the point. Every time you take damage or one of your big characters gets knocked out, you draw a card. The deck is built to make those trades hurt the opponent more than they hurt you. Once you understand that, Ace stops feeling fragile and starts feeling inevitable."),
    ("OP13-016", "Your turn one priority. Garp gets the early hand-building started so you're not scrambling to find your 6000-power bodies in the midgame. Ace wants to hit the ground running — Garp is a big part of how that happens consistently."),
    ("OP13-043", "One of the sneakiest cards in the deck. Ace starts at 3 Life, so Otama's draw-2-trash-1 effect is live from the very first turn. Early hand fixing that costs almost nothing — experienced players look for this in their opening hand every game."),
    ("ST22-002", "Consistency piece that shows up in most current Ace lists. Helps you find the key Whitebeard Pirates pieces you need for the midgame. The deck plays a clean curve when Izo is doing its job early — without it the setup can feel a little scattered."),
    ("OP13-054", "A card that does three things at once — draws cards, interacts with the board, and attaches DON!! back to your Leader. That last part matters a lot. The Leader effect only works when DON!! is attached, so Yamato basically keeps the engine running while also developing your board. One of the smoothest cards in the deck."),
    ("OP13-119", "The character version of Ace is everything the deck wants in one card. Draw, removal, and DON!! reattachment all on a single On Play effect. If you're new to the deck and wondering why it feels so cohesive, this card is a big part of the answer."),
    ("PRB02-008", "The heart of the deck's defense. Marco is a Blocker that draws 2 when he gets knocked out — and because Ace's Leader also draws when a 6000+ power character is KO'd, Marco dying can mean 3 cards drawn at once. Opponents hate removing him and they hate leaving him up. There's no good answer."),
    ("OP08-047", "Keeps the 6000+ power body count high so the Leader effect stays active throughout the game. Ace needs multiple big bodies on the board at all times — Jozu is one of the cards that makes sure that's always the case even after trades happen."),
    ("OP13-042", "Your late game anchor. Twelve thousand power Blocker that on entry reloads your Leader's DON!!, strengthens another character, and improves your hand. It doesn't just defend — it sets up your next turn at the same time. One of the strongest late game cards in the format right now."),
    ("ST22-015", "One of the glue Events that makes Ace's defense reliable. The deck draws a lot of cards but it still needs smart defensive tools to survive into the late game. This is one of them — don't underestimate how much it stabilizes close games."),
    ("OP04-056", "Efficient removal that shows up in most current Ace lists. When your draw engine is running hot you need clean answers to big threats — Red Roc is that answer. Keeps your tempo up while the value engine keeps doing its thing in the background."),
    ("OP13-057", "This one catches opponents off guard. You can use it as a counter early, but when you're down to 1 or less Life it becomes something else entirely — your Leader attacks and the opponent can't activate Blockers. A lot of games get stolen by this card right when the opponent thinks they've got it locked up. Always keep it in mind late game."),
    ("OP14-020", "Mihawk looks like he has a big drawback — activate the Leader effect and you can't play Characters that turn. But the deck is built so that barely matters. You're spending that refreshed DON!! on Events and effects that do real work anyway. Once the engine clicks you start to feel like the opponent is always one step behind, never quite able to get comfortable."),
    ("OP12-034", "The deck's consistency engine. On play she looks at the top 5 cards and grabs a Slash character or green Event — exactly the pieces Mihawk needs to keep the rest-and-reactivate plan running. Without her the deck can feel scattered. With her it feels like everything shows up right when you need it."),
    ("OP14-039", "Quietly one of the best cards in the shell. Draw a card when it enters, then at the end of your turn it sets a DON!! active. Free value that works with the rest of the engine instead of against it. Experienced Mihawk players treat this as one of the non-negotiables in the list."),
    ("OP14-029", "Does two important things at once. She's a 5-cost Character which immediately turns the Leader effect on — and she rewards you for resting your own cards by gaining power and protecting herself from removal. In Mihawk, resting your cards is the whole gameplan. Tashigi fits that perfectly."),
    ("OP14-027", "One of the nastiest interactions in the deck. When Mihawk rests Shanks with the Leader effect, Shanks immediately rests one of the opponent's Characters with 7000 power or less — and while Shanks stays rested, all opposing Characters lose 1000 power. So your Leader activation becomes board control plus DON!! recovery at the same time. Opponents really don't enjoy this."),
    ("OP14-119", "The finisher the whole deck builds toward. When this Mihawk becomes rested, one opposing Character with cost 9 or less can't become active next turn. The Leader rests him the turn you play him — so he's locking things down immediately, no waiting. Once he's on the field the opponent's options start shrinking fast."),
    ("ST24-004", "A high-end pressure card that punishes what Mihawk already creates. It rests an opposing Character on entry and keeps it locked through their next refresh. If the opponent already has 2 or more rested Characters — which happens a lot in this matchup — your Leader gains 2000 power on top of that. Converts board control into real damage."),
    ("OP06-038", "A counter Event that scales with how well you're executing the gameplan. Base effect is plus 2000 in battle — but if you have 8 or more rested cards it jumps to plus 4000. Mihawk ends up with a lot of rested cards naturally, so hitting that bonus is much more realistic here than in most other decks. Keep a couple in hand for the right moment."),
    ("OP12-037", "Flexible tool that fits the Mihawk shell well. The main effect lets you spend rested DON!! to rest up to 2 opposing Characters or DON!! cards — which keeps opponents off balance without needing to play a Character. The counter gives plus 3000 which is strong on its own. This card lets Mihawk apply pressure on turns where the Leader effect already fired."),
    ("OP14-041", "Boa Hancock flips how you think about Life cards. In most decks Life is just your health bar — here it's loaded with Characters waiting to hit the field. Every time the opponent attacks and triggers one, you draw a card. They're essentially helping you build your board. Once that clicks, you start to understand why opponents get frustrated playing against this deck."),
    ("OP06-106", "One of the most important setup cards in the deck. She lets you swap a card from your Life with one from your hand — which means you get to choose what's waiting in your Life pile. Boa wants specific Trigger cards loaded in there. Hiyori is how you make sure the right ones are ready."),
    ("OP14-103", "Does a similar job to Hiyori — helps you move cards between hand and Life so your Trigger pile stays useful. The bonus is she's also a Trigger herself, so she can enter play off a Life hit and immediately start helping. Double duty cards like this are exactly what the deck wants."),
    ("OP14-113", "Never doing just one job. On play she searches the top 5 for an Amazon Lily or Kuja Pirates card, and she's also a Trigger body herself. So she finds your pieces when played naturally and contributes to your board when triggered. One of the cleanest consistency cards in the whole shell."),
    ("ST17-004", "Solid blocker that also smooths your draws and supports the DON!! plan. Boa decks need cards that are useful in a variety of situations — this one delivers whether you draw it early, late, or find it mid-grind. Gives the deck stability when the Trigger engine hasn't fully come online yet."),
    ("OP14-114", "A 5000-power Trigger body that gives a rested DON!! to your Kuja Pirates Leader or Character on entry. That DON!! keeps the Leader effect live, which is everything in this deck. She's also exactly the kind of body your opponent doesn't want to KO — which means she often sits on the field longer than she should."),
    ("OP14-105", "This is how small Trigger pieces become real board pressure. Reveal 3 Amazon Lily or Kuja Pirates cards from hand and every Character you control gets a rested DON!! — including your Leader. It's also a Trigger, so it can enter off a Life hit and immediately boost the whole board. One of the most impactful cards in the deck when it goes off at the right moment."),
    ("OP14-107", "Hand quality insurance. If the opponent is at 3 or less Life she draws 2 and trashes 2 on entry — keeping your hand clean and focused instead of flooding out with cards you don't need. She's a Trigger body too, so late game Life hits can suddenly give you two fresh cards and clear the clutter. Underrated by players new to the deck."),
    ("OP12-119", "One of the upgrades that made current Hancock lists noticeably tougher. He adds a card to Life when he enters, and if he gets KO'd on the opponent's turn he adds another one. So he keeps the Trigger engine loaded from both directions — alive or removed, he's doing work. A big reason the deck feels more resilient than older versions."),
    ("OP14-112", "The card the whole deck is building toward. On play it adds a card to your Life and sends one of the opponent's Life cards to their hand — a huge swing in both directions at once. Its Trigger also plays a Character with 6000 power or less that has Trigger from hand. When this lands at the right moment it can completely flip the game state. Everything else in the deck exists to make this hit harder."),
    ("OP14-108", "One of the nastiest cards to hit off a Trigger. If the opponent is at 3 or less Life, his On Play KOs a Character with 7000 base power or less — and his Trigger does the same thing. So a Life hit in the late game can become free removal on a key body. Opponents at low Life learn very quickly to fear what's sitting in your Life pile."),
    ("OP14-118", "A Trigger Event that plays a 6000-power-or-less Trigger Character from your hand when it hits. Basically turns your hand into a second Life engine — the opponent attacks, triggers this, and you get to develop a body anyway. Adds another layer to the deck's core loop and makes your hand feel live even when Life is running low."),
    ("OP15-058", "Enel plays by different rules. Most decks have 10 DON!! — Enel only has 6, but the Leader effect can add 5 of them back in a single turn and attach 4 rested DON!! straight onto a Character. That means spending DON!! on Events and effects isn't really a cost here. The deck recovers almost immediately every time. Once you see it in action it makes total sense why it's already making waves in Japan."),
    ("OP15-061", "One of your four core early Vassals. On his own he's a 2000-power body — but when Enel loads him with 4 DON!!, he's swinging at 6000. His On Play effect also spends 1 DON!! to draw a card, so he replaces himself on entry. Small, self-replacing, and immediately dangerous with the Leader online. Exactly what the deck needs in the early turns."),
    ("OP15-063", "Another key Vassal in the early package. Same deal as Ohm — baseline 2000 power, becomes a real threat when the Leader attaches DON!!, and draws a card on play. The deck wants to see these Vassals consistently in the opening hand, not just occasionally. Gedatsu is part of why the turn 2 Leader activation is so reliable."),
    ("OP15-066", "Third of the four core Vassals. The early game plan isn't built around one special setup card — it's built around seeing enough of these cheap bodies that the Leader always has something good to work with. Satori keeps that engine consistent and makes the deck's explosive early turns repeatable instead of lucky."),
    ("OP15-067", "The fourth Vassal in the official core package. Same role as the others — cheap body that cycles your hand and becomes a real attacker once Enel starts attaching DON!!. If you're new to the deck, mulliganing for at least two of these four Vassals gives you the opening the deck is designed around."),
    ("OP15-071", "The card that turns your small Vassal board into something genuinely scary. Holly gives herself and all your Ohm cards Double Attack, and on the opponent's turn they all become 6000 base power. So what looked like a pile of 2000-power bodies suddenly hits twice and defends at 6000. Opponents who don't respect the early board learn quickly."),
    ("OP15-118", "One of your two main payoff threats. While you have 6 or less DON!! — which is basically always in this deck — he becomes a 10000-power attacker that can't be removed by opponent effects. On play he also looks at the top 5 and adds a card to hand for 1 DON!!. Pressure and consistency in one card. This is usually how you find the next Enel to keep the chain going."),
    ("OP15-060", "The other big Enel threat. Same 10000-power and effect-removal immunity while you're at 6 or less DON!!. His Activate Main gives him Blocker for 1 DON!! — so he can swing hard on your turn and then step in front of a big attack on theirs. The deck can shift from aggression to defense without losing momentum because of this card."),
    ("OP15-078", "One of the most important Events in the deck. Main effect spends 2 DON!! to draw a card and rest an opposing Character with 5000 power or less. Counter effect gives plus 1000 power — and if you have 6 or less DON!! it also draws a card. Useful on both turns, in both directions. This is the cleanest example of how Enel turns DON!! spending into tempo instead of loss."),
    ("OP15-077", "Zero cost Event that spends 1 DON!! to draw a card and keep a rested opposing Character with 6000 power or less from becoming active next refresh. So after Mamaragan rests something, Lightning Dragon locks it down. One of the best tempo-control tools in the deck — opponents hate watching their key bodies stay rested turn after turn."),
    ("OP15-076", "Zero cost Event that spends 1 DON!! to draw a card and give an opposing Character minus 1000 power for the turn. Works especially well alongside Mamaragan — lower the power threshold first, then rest the body that was just out of range. Cheap, flexible, and keeps the combat math in your favor without committing much."),
    ("OP15-075", "Zero cost Event that spends 1 DON!! to give one of your Leader or Characters plus 1000 power and KO an opposing Character with 3000 power or less. Cheap interaction that clears small utility bodies while pushing your attacks through. Keeps the board clean without slowing the pressure down."),
    ("OP07-064", "A flex include that fits naturally into Enel's economy. If your DON!! count is at least 2 lower than your opponent's — which happens constantly here — Sanji costs 3 less and comes with Blocker. In most decks that condition is awkward to hit. In Enel it's almost automatic. Gives the deck one more strong defensive body that fits the same lower-DON profile everything else runs on."),
]


def _row_has_extra_columns(conn: sqlite3.Connection) -> tuple[bool, bool]:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(miru_card_insights)").fetchall()}
    return ("approval_state" in cols, "is_upcoming" in cols)


def main() -> int:
    db_path = Path(DEFAULT_PROJECT_DB_PATH)
    ensure_catalog_sync_schema(db_path)

    inserted = 0
    skipped = 0
    replaced = 0
    errors: list[str] = []
    affected_for_queue: list[str] = []

    with closing(connect_catalog_db(db_path)) as conn:
        _ensure_column(conn, "miru_card_insights", "approval_state TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_card_insights", "is_upcoming INTEGER NOT NULL DEFAULT 0")
        has_approval, has_upcoming = _row_has_extra_columns(conn)

        now_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        gen_at = int(time.time())

        for card_id, insight_text in INSIGHT_ROWS:
            cid = card_id.strip().upper()
            row = conn.execute(
                "SELECT confidence FROM miru_card_insights WHERE card_id = ? AND insight_type = ?",
                (cid, INSIGHT_TYPE),
            ).fetchone()
            tier = classify_insight_quality(insight_text, CONFIDENCE_TARGET)

            if row is not None:
                prev_conf = float(row["confidence"])
                if prev_conf >= CONFIDENCE_TARGET:
                    print(f"SKIPPED (already >= {CONFIDENCE_TARGET}): {cid} ({INSIGHT_TYPE}) conf={prev_conf}")
                    skipped += 1
                    continue
                # replace
                sets = [
                    "insight_text = ?",
                    "confidence = ?",
                    "quality_tier = ?",
                    "source_ref = ?",
                    "leader_code = ?",
                    "used_sections_json = ?",
                    "sync_reason = ?",
                    "source_updated_at = ?",
                    "generated_at = ?",
                    "updated_at = ?",
                ]
                vals: list = [
                    insight_text,
                    CONFIDENCE_TARGET,
                    tier,
                    SOURCE_REF,
                    "",
                    "[]",
                    SYNC_REASON,
                    "",
                    gen_at,
                    now_ts,
                ]
                if has_approval:
                    sets.append("approval_state = ?")
                    vals.append("")
                if has_upcoming:
                    sets.append("is_upcoming = ?")
                    vals.append(0)
                vals.extend([cid, INSIGHT_TYPE])
                conn.execute(
                    f"UPDATE miru_card_insights SET {', '.join(sets)} WHERE card_id = ? AND insight_type = ?",
                    vals,
                )
                print(f"REPLACED: {cid} (was conf={prev_conf})")
                replaced += 1
                affected_for_queue.append(cid)
                continue

            # insert — FK requires card in cards
            exists = conn.execute(
                "SELECT 1 FROM cards WHERE canonical_code = ? LIMIT 1",
                (cid,),
            ).fetchone()
            if not exists:
                errors.append(f"MISSING_CARD_IN_CATALOG: {cid}")
                continue

            cols = [
                "card_id",
                "insight_type",
                "insight_text",
                "confidence",
                "quality_tier",
                "source_ref",
                "leader_code",
                "used_sections_json",
                "sync_reason",
                "source_updated_at",
                "generated_at",
                "updated_at",
            ]
            ins_vals = [
                cid,
                INSIGHT_TYPE,
                insight_text,
                CONFIDENCE_TARGET,
                tier,
                SOURCE_REF,
                "",
                "[]",
                SYNC_REASON,
                "",
                gen_at,
                now_ts,
            ]
            if has_approval:
                cols.append("approval_state")
                ins_vals.append("")
            if has_upcoming:
                cols.append("is_upcoming")
                ins_vals.append(0)

            placeholders = ", ".join(["?"] * len(ins_vals))
            conn.execute(
                f"INSERT INTO miru_card_insights ({', '.join(cols)}) VALUES ({placeholders})",
                ins_vals,
            )
            inserted += 1
            affected_for_queue.append(cid)
            print(f"INSERTED: {cid}")

        conn.commit()

    # Governance: enqueue review for inserted/replaced cards (normal pending_review path)
    queue_ok = 0
    queue_fail = 0
    if affected_for_queue:
        try:
            from tools.miru_action_governance import (  # noqa: E402
                _upsert_publication_readiness,
                _upsert_review_queue_entry,
                build_publication_candidate_summary,
            )

            with closing(connect_catalog_db(db_path)) as conn:
                for code in affected_for_queue:
                    try:
                        summary = build_publication_candidate_summary(card_code=code, project_db_path=db_path)
                        if not str(summary.get("card_code") or "").strip():
                            queue_fail += 1
                            continue
                        _upsert_review_queue_entry(
                            conn,
                            summary=summary,
                            forced=True,
                            note="operator_card_insight_ingest",
                            decision_source=SYNC_REASON,
                        )
                        _upsert_publication_readiness(conn, {**summary, "approval_state": "pending_review"})
                        queue_ok += 1
                    except Exception as exc:  # noqa: BLE001
                        print(f"QUEUE_WARN {code}: {exc}")
                        queue_fail += 1
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"QUEUE_PHASE_FAILED: {exc}")

    print()
    print("=== SUMMARY ===")
    print(f"inserted: {inserted}")
    print(f"skipped (existing conf >= {CONFIDENCE_TARGET}): {skipped}")
    print(f"replaced: {replaced}")
    print(f"card_ids in batch: {len(INSIGHT_ROWS)}")
    print(f"errors: {len(errors)}")
    for e in errors[:20]:
        print(f"  {e}")
    print(f"review_queue upserts attempted OK: {queue_ok} fail: {queue_fail}")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
