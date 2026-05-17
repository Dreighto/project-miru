# PRO-904 — OP01 Bandai crawl vs DB diff

## Counts

- Crawl printings total: **218**
- DB variants total (OP01-%): **348**
  - Bandai-format print_id (`OP01-NNN` / `OP01-NNN_pN`): **235**
  - Synthetic `::`-style print_id (legacy): **113**

## Diff (on Bandai-format keys)

- **Matched** (in both crawl and DB): **218**
- **Candidate missing** (in crawl, not in DB): **0**
- **Candidate phantom** (in DB Bandai-format, not in crawl): **17**
- **Synthetic legacy** (DB non-Bandai-format, not part of diff key): **113**

## Candidate phantom (DB Bandai-format has, Bandai doesn't)

| card_number | print_id | variant_label | release_set | image_url |
| --- | --- | --- | --- | --- |
| OP01-006 | OP01-006_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-006_r1.png?260305 |
| OP01-024 | OP01-024_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-024_r1.png?260305 |
| OP01-029 | OP01-029_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-029_r1.png?260305 |
| OP01-033 | OP01-033_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-033_r1.png?260305 |
| OP01-039 | OP01-039_r1 | R1 | PRB02 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-039_r1.png?260305 |
| OP01-041 | OP01-041_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-041_r1.png?260305 |
| OP01-047 | OP01-047_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-047_r1.png?260305 |
| OP01-051 | OP01-051_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-051_r1.png?260305 |
| OP01-052 | OP01-052_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-052_r1.png?260305 |
| OP01-055 | OP01-055_r1 | R1 | PRB02 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-055_r1.png?260305 |
| OP01-070 | OP01-070_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-070_r1.png?260305 |
| OP01-073 | OP01-073_r1 | R1 | ST17 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-073_r1.png?260305 |
| OP01-078 | OP01-078_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-078_r1.png?260305 |
| OP01-086 | OP01-086_r1 | R1 | ST17 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-086_r1.png?260305 |
| OP01-120 | OP01-120_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-120_r1.png?260305 |
| OP01-120 | OP01-120_r2 | R2 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-120_r2.png?260305 |
| OP01-121 | OP01-121_r1 | R1 | PRB01 | https://en.onepiece-cardgame.com/images/cardlist/card/OP01-121_r1.png?260305 |

## Synthetic legacy rows (DB non-Bandai-format print_id)

_113 rows. Not part of the (card_number, print_id) diff key —
these are legacy `::`-style entries that the three OP01 remediation passes
will reconcile separately. Listed here for visibility._

| card_number | print_id | variant_label | release_set_name |
| --- | --- | --- | --- |
| OP01-001 | OP01-001::25th | 25Th | Romance Dawn |
| OP01-001 | OP01-001::alt | Alt | Romance Dawn |
| OP01-002 | OP01-002::alt | Alt | Romance Dawn |
| OP01-003 | OP01-003::alt | Alt | Romance Dawn |
| OP01-004 | OP01-004::1st anniversary alt | 1St Anniversary Alt | Romance Dawn |
| OP01-004 | OP01-004::alt | Alt | Romance Dawn |
| OP01-005 | OP01-005::alt | Alt | Romance Dawn |
| OP01-005 | OP01-005::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-005 | OP01-005::film | Film | Romance Dawn |
| OP01-005 | OP01-005::film2 | Film2 | Romance Dawn |
| OP01-006 | OP01-006::alt | Alt | Romance Dawn |
| OP01-006 | OP01-006::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-006 | OP01-006::prb01alt1 | Prb01Alt1 | Romance Dawn |
| OP01-006 | OP01-006::prb01alt2 | Prb01Alt2 | Romance Dawn |
| OP01-008 | OP01-008::alt | Alt | Romance Dawn |
| OP01-013 | OP01-013::1st anniversary alt | 1St Anniversary Alt | Romance Dawn |
| OP01-013 | OP01-013::25th | 25Th | Romance Dawn |
| OP01-013 | OP01-013::alt | Alt | Romance Dawn |
| OP01-013 | OP01-013::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-013 | OP01-013::alt art 3 | Alt Art 3 | Romance Dawn |
| OP01-013 | OP01-013::tr alt | Tr Alt | Romance Dawn |
| OP01-014 | OP01-014::film | Film | Romance Dawn |
| OP01-014 | OP01-014::film2 | Film2 | Romance Dawn |
| OP01-015 | OP01-015::alt | Alt | Romance Dawn |
| OP01-016 | OP01-016::1st anniversary alt | 1St Anniversary Alt | Romance Dawn |
| OP01-016 | OP01-016::25th | 25Th | Romance Dawn |
| OP01-016 | OP01-016::alt | Alt | Romance Dawn |
| OP01-016 | OP01-016::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-016 | OP01-016::alt art 3 | Alt Art 3 | Romance Dawn |
| OP01-016 | OP01-016::alt art 4 | Alt Art 4 | Romance Dawn |
| OP01-016 | OP01-016::alt art 7 | Alt Art 7 | Romance Dawn |
| OP01-016 | OP01-016::manga alt | Manga Alt | Romance Dawn |
| OP01-016 | OP01-016::st10 alt | St10 Alt | Romance Dawn |
| OP01-017 | OP01-017::1st anniversary alt | 1St Anniversary Alt | Romance Dawn |
| OP01-017 | OP01-017::alt | Alt | Romance Dawn |
| OP01-017 | OP01-017::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-017 | OP01-017::film | Film | Romance Dawn |
| OP01-017 | OP01-017::film2 | Film2 | Romance Dawn |
| OP01-021 | OP01-021::alt | Alt | Romance Dawn |
| OP01-021 | OP01-021::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-021 | OP01-021::film | Film | Romance Dawn |
| OP01-021 | OP01-021::film2 | Film2 | Romance Dawn |
| OP01-021 | OP01-021::fullalt | Fullalt | Romance Dawn |
| OP01-022 | OP01-022::25th | 25Th | Romance Dawn |
| OP01-024 | OP01-024::alt | Alt | Romance Dawn |
| OP01-024 | OP01-024::prb01alt | Prb01Alt | Romance Dawn |
| OP01-025 | OP01-025::1st anniversary alt | 1St Anniversary Alt | Romance Dawn |
| OP01-025 | OP01-025::alt | Alt | Romance Dawn |
| OP01-025 | OP01-025::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-025 | OP01-025::alt st10 | Alt St10 | Romance Dawn |
| OP01-025 | OP01-025::winner alt | Winner Alt | Romance Dawn |
| OP01-029 | OP01-029::alt | Alt | Romance Dawn |
| OP01-030 | OP01-030::2nd anniversary alt | 2Nd Anniversary Alt | Romance Dawn |
| OP01-031 | OP01-031::alt | Alt | Romance Dawn |
| OP01-033 | OP01-033::alt | Alt | Romance Dawn |
| OP01-033 | OP01-033::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-033 | OP01-033::prb01alt | Prb01Alt | Romance Dawn |
| OP01-034 | OP01-034::alt | Alt | Romance Dawn |
| OP01-035 | OP01-035::alt | Alt | Romance Dawn |
| OP01-035 | OP01-035::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-035 | OP01-035::s p | S P | Romance Dawn |
| OP01-039 | OP01-039::alt | Alt | Romance Dawn |
| OP01-040 | OP01-040::alt | Alt | Romance Dawn |
| OP01-041 | OP01-041::alt | Alt | Romance Dawn |
| OP01-041 | OP01-041::prb01alt | Prb01Alt | Romance Dawn |
| OP01-041 | OP01-041::w inner alt | W Inner Alt | Romance Dawn |
| OP01-047 | OP01-047::alt | Alt | Romance Dawn |
| OP01-047 | OP01-047::prb01alt2 | Prb01Alt2 | Romance Dawn |
| OP01-047 | OP01-047::s p | S P | Romance Dawn |
| OP01-048 | OP01-048::alt | Alt | Romance Dawn |
| OP01-051 | OP01-051::alt | Alt | Romance Dawn |
| OP01-051 | OP01-051::prb01alt | Prb01Alt | Romance Dawn |
| OP01-051 | OP01-051::wanted | Wanted | Romance Dawn |
| OP01-052 | OP01-052::alt | Alt | Romance Dawn |
| OP01-052 | OP01-052::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-052 | OP01-052::prb01alt | Prb01Alt | Romance Dawn |
| OP01-057 | OP01-057::alt | Alt | Romance Dawn |
| OP01-060 | OP01-060::alt | Alt | Romance Dawn |
| OP01-060 | OP01-060::st17alt | St17Alt | Romance Dawn |
| OP01-061 | OP01-061::alt | Alt | Romance Dawn |
| OP01-062 | OP01-062::alt | Alt | Romance Dawn |
| OP01-064 | OP01-064::alt | Alt | Romance Dawn |
| OP01-067 | OP01-067::alt | Alt | Romance Dawn |
| OP01-070 | OP01-070::alt | Alt | Romance Dawn |
| OP01-070 | OP01-070::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-070 | OP01-070::prb01alt | Prb01Alt | Romance Dawn |
| OP01-073 | OP01-073::alt | Alt | Romance Dawn |
| OP01-073 | OP01-073::s p | S P | Romance Dawn |
| OP01-077 | OP01-077::alt | Alt | Romance Dawn |
| OP01-077 | OP01-077::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-077 | OP01-077::alt art 3 | Alt Art 3 | Romance Dawn |
| OP01-078 | OP01-078::alt | Alt | Romance Dawn |
| OP01-078 | OP01-078::prb01alt | Prb01Alt | Romance Dawn |
| OP01-078 | OP01-078::s p | S P | Romance Dawn |
| OP01-079 | OP01-079::alt | Alt | Romance Dawn |
| OP01-091 | OP01-091::alt | Alt | Romance Dawn |
| OP01-093 | OP01-093::alt | Alt | Romance Dawn |
| OP01-093 | OP01-093::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-094 | OP01-094::alt | Alt | Romance Dawn |
| OP01-094 | OP01-094::alt art 2 | Alt Art 2 | Romance Dawn |
| OP01-096 | OP01-096::alt | Alt | Romance Dawn |
| OP01-101 | OP01-101::alt | Alt | Romance Dawn |
| OP01-102 | OP01-102::alt | Alt | Romance Dawn |
| OP01-108 | OP01-108::alt | Alt | Romance Dawn |
| OP01-109 | OP01-109::alt | Alt | Romance Dawn |
| OP01-114 | OP01-114::alt | Alt | Romance Dawn |
| OP01-120 | OP01-120::alt | Alt | Romance Dawn |
| OP01-120 | OP01-120::manga | Manga | Romance Dawn |
| OP01-120 | OP01-120::prb01alt | Prb01Alt | Romance Dawn |
| OP01-120 | OP01-120::s n | S N | Romance Dawn |
| OP01-121 | OP01-121::a t2 | A T2 | Romance Dawn |
| OP01-121 | OP01-121::alt | Alt | Romance Dawn |
| OP01-121 | OP01-121::prb01alt | Prb01Alt | Romance Dawn |
