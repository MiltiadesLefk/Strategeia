# Third-party notices

Strategeia is written for this project, but some files are copied or adapted from other open-source
projects. This file records **which files**, **where they came from**, and **the license terms that
travel with them**. It was started before the first copied file landed, so every copy is recorded from
day one.

**The rule** (plan.md §1): if a project's license lets us copy its code (MIT, Apache-2.0), we copy it
and keep its notices. If it doesn't (for example AGPL-3.0), we take only the idea and write our own
code. Taking only an idea needs no row here, just an `idea from <repo>` comment in our code.

**Every copied or adapted file** (a TypeScript-to-Python port and adapted prompt text count too):

1. keeps the original copyright and license notice at the top. If the source file has its own header,
   keep it word for word;
2. gets one added line: `Adapted from <repo>@<short-commit> <path>; changes: <...>`. For Apache-2.0
   sources this line is also the "this file was changed" notice that section 4(b) requires;
3. gets a row in the table below.

Header template (Python shown; use `//` in TypeScript):

```python
# Copyright (c) 2025 LLMQuant. MIT License; full text in THIRD_PARTY_NOTICES.md.
# Adapted from LLMQuant/quant-mind@10e9dbd <path>; changes: <what we changed>
```

The three Apache-2.0 projects below name no copyright holder in their LICENSE, so name the project:

```python
# From TauricResearch/TradingAgents (via the vongchu/TradingAgents_TauricResearch mirror).
# Licensed under the Apache License, Version 2.0; full text in THIRD_PARTY_NOTICES.md.
# Adapted from vongchu/TradingAgents_TauricResearch@01477f9 <path>; changes: <what we changed>
```

---

## Files adapted from other projects

**To add a file:** append one row at the bottom (delete the "(none yet)" row with the first real one).
The short commit is the first 7 characters of the pinned commit in the next section. If the project
isn't listed there, add it first, with its license text at the bottom of this file if the copyright
holder is new.

| Our file | Source (repo@short-commit) | Source file | License | What we changed |
|---|---|---|---|---|
| `backend/app/services/lesson_service.py` | vongchu/TradingAgents_TauricResearch@01477f9 | `tradingagents/graph/reflection.py` | Apache-2.0 | Kept the idea and wording shape of the short review prompt (2-4 plain sentences: was the call right, what held, one lesson), reworded; everything else is new (facts-only prompt built from our trade records, SPY comparison from real bars, untrusted-text handling, failure handling, scheduling). |
| `frontend/src/components/CommandPalette.tsx` | ErTasselli/OpenTerminal@95618ee | `web/components/CommandPalette.tsx` | MIT | Kept the shape (query and selected-row state, Esc / Up / Down / Enter handling, overlay with click-outside to close). Searches our own symbol universe in the browser with a new fuzzy scorer instead of calling a search endpoint; added page and "generate trade plan" rows, recent symbols, a focus trap, combobox/listbox accessibility roles, a phone layout, react-router navigation and our CSS. |
| `frontend/src/components/Flash.tsx`, `frontend/src/lib/useFlash.ts` | ErTasselli/OpenTerminal@95618ee | `web/components/Flash.tsx` | MIT | Kept the idea and shape (a hook that compares the value with its previous render and flashes briefly, plus a wrapper span). Flashes green for up and red for down instead of white, never on first mount or when the subject (symbol) changes, honours reduced motion, and replays when changes arrive back to back; the change test is a separate unit-tested function. |
| `backend/app/data_providers/finra_provider.py` | ErTasselli/OpenTerminal@95618ee | `server/src/providers/finra.ts` | MIT | Kept the idea and URL pattern (one pipe-delimited FINRA Reg SHO file per trading day, parsed once and looked up by symbol). Ported to Python; the parser tolerates header, trailer, blank and malformed lines, several symbols are looked up at once, a date range is walked, downloaded files are kept on disk (a posted file never changes), and a missing day (weekend, holiday) is a normal `None` rather than an error. |
| `frontend/src/components/smartmoney/InsiderTradesTable.tsx` | ErTasselli/OpenTerminal@95618ee | `web/components/widgets/InsiderWidget.tsx` | MIT | Kept the idea and the plain-language labels for SEC transaction codes in an insider table. Rows come from our stored, dated Form 4 filings; added the trade date next to the SEC acceptance date with the filing delay, role badges, a pre-arranged-plan chip, a source link and a no-price display. |

---

## Projects we may copy from

The pinned commit is the exact version we read. If you copy from a newer commit, use that commit in
your row and re-check the project's LICENSE and NOTICE files.

**Apache-2.0 NOTICE files (section 4(d)):** none of the three Apache-2.0 projects has a NOTICE file at
its pinned commit (their full file trees were checked on 2026-09-28), so there is no NOTICE text to
carry. Re-check if you move to a newer commit.

| Project | Repository | License | Pinned commit | Copyright holder, as written in its LICENSE |
|---|---|---|---|---|
| TradingAgents | https://github.com/vongchu/TradingAgents_TauricResearch | Apache-2.0 | `01477f9afb7a47b849ed4c9259d3a9a4738d9fda` | None named (see notes) |
| Anthropic finance skills | https://github.com/anthropics/financial-services | Apache-2.0 | `574ed3624aebd0418c7e96cd101262f30210ab26` | None named (see notes) |
| Phil | https://github.com/bennyjo/phil | Apache-2.0 | `bf3737db6b8256ced7b211204fbb17b3fb06960d` | None named (see notes) |
| Kronos | https://github.com/shiyu-coder/Kronos | MIT | `67b630e67f6a18c9e9be918d9b4337c960db1e9a` | `Copyright (c) 2025 ShiYu` |
| ML4T | https://github.com/stefan-jansen/machine-learning-for-trading | MIT | `dd5ec27aaee25d5d6c4017014a544cd87236f0ce` | `Copyright (c) 2024-2026 Stefan Jansen` |
| OpenTerminal | https://github.com/ErTasselli/OpenTerminal | MIT | `95618eed88ff331e8fb48ae01dcc3ea88e3d00cc` | `Copyright (c) 2026 OpenTerminal contributors` |
| quant-mind | https://github.com/LLMQuant/quant-mind | MIT | `10e9dbd0c7254edc5a091b852122ebe72e06092a` | `Copyright (c) 2025 LLMQuant` |
| jev-trade | https://github.com/aowang-ai/jev-trade | MIT | `a3f2f834a1b97dd42fab1193814179ac2e96d7cd` | `Copyright (c) 2026 aowang` (plus a jev-trader credit; see notes) |

**Notes per project**

- **TradingAgents:** the repository is a mirror of https://github.com/TauricResearch/TradingAgents at
  release v0.3.1. Its LICENSE leaves the Apache template line `Copyright [yyyy] [name of copyright
  owner]` unfilled. The project is Tauric Research's (its command-line tool prints "© Tauric
  Research").
- **Anthropic finance skills:** the top-level LICENSE leaves the template copyright line unfilled; the
  repository belongs to the `anthropics` GitHub organization. The skills plan.md uses
  (`plugins/vertical-plugins/*/skills/`) fall under that top-level LICENSE, with one exception:
  `plugins/vertical-plugins/financial-analysis/skills/skill-creator/` has its own `LICENSE.txt` (also
  Apache-2.0, template line unfilled). The folder `plugins/partner-built/spglobal/` and its skills
  have their own Apache-2.0 LICENSE files that read `Copyright 2026-present Kensho Technologies,
  LLC.`; anything copied from there must carry that line.
- **Phil:** the LICENSE leaves the template copyright line unfilled. Its README says the journal and
  strategy files are covered by the same license.
- **Kronos:** the trained model weights are published separately on Hugging Face (NeoQuasar), not in
  this repository. Check the model card's license before using them.
- **ML4T:** its code depends on separate `ml4t-*` libraries. Check each one's license before we depend
  on it.
- **jev-trade:** its LICENSE also says: "This repository is based on jev-trader by Jarrod Watts
  (https://github.com/jarrodwatts/jev-trader), used under the MIT License." jev-trader's own LICENSE
  (checked at commit `b587759e459ea049590102e54a0b07800864cdc3` on 2026-09-28) reads
  `Copyright (c) 2026 Jarrod Watts`. Our clone of jev-trade has no history, so we can't tell which of
  its files came from jev-trader: a file copied from jev-trade carries **both** copyright lines.

---

## Not copied (ideas only)

| Project | License | What we do |
|---|---|---|
| OpenStock (https://github.com/Open-Dev-Society/OpenStock) | AGPL-3.0 | Ideas only. Never copy its code. |
| NautilusTrader (https://github.com/nautechsystems/nautilus_trader) | LGPL-3.0 | Ideas only for now. Using it later as an installed library would be fine; any source we copied from it would stay under LGPL-3.0. |

## Used while the app runs (no code copied)

| What | How the app uses it | License or terms |
|---|---|---|
| Yahoo Finance data, through the `yfinance` library | Installed from PyPI (`backend/requirements.txt`), not copied. It is the main market-data source. | `yfinance` is Apache-2.0. The data itself is Yahoo's and falls under Yahoo's terms. |
| Elbstream Stock Logo API | `frontend/src/components/CompanyIcon.tsx` loads stock logos from `api.elbstream.com/logos/symbol/<TICKER>`. | The free tier requires a visible credit. The sidebar footer shows "Logos by Elbstream", linking to elbstream.com/logos (`frontend/src/layout/Sidebar.tsx`). |
| `spothq/cryptocurrency-icons` | `CompanyIcon.tsx` loads icons for crypto symbols (`<COIN>-USD`) from jsDelivr (`cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/128/color`). | CC0-1.0, a public-domain dedication (checked on GitHub, 2026-09-28). No credit required. |

Other Python and npm packages are installed as normal dependencies, not copied; each keeps its own
license inside its package, so they aren't listed here.

---

## License texts

The license texts below are reproduced exactly as they appear in each project's LICENSE file at the
pinned commit (only line endings normalised). MIT requires the copyright line and permission notice to
travel with every copy, so there is one MIT text per copyright holder.

### Apache License, Version 2.0

Applies to TradingAgents, the Anthropic finance skills and Phil. (Their three LICENSE files hold the
same text; the `Copyright [yyyy] [name of copyright owner]` line in the appendix is the license's
own template, left unfilled by all three.)

```text
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright [yyyy] [name of copyright owner]

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
```

### MIT License: Kronos

From `shiyu-coder/Kronos`.

```text
MIT License

Copyright (c) 2025 ShiYu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### MIT License: ML4T

From `stefan-jansen/machine-learning-for-trading`.

```text
MIT License

Copyright (c) 2024-2026 Stefan Jansen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### MIT License: OpenTerminal

From `ErTasselli/OpenTerminal`.

```text
MIT License

Copyright (c) 2026 OpenTerminal contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### MIT License: quant-mind

From `LLMQuant/quant-mind`.

```text
MIT License

Copyright (c) 2025 LLMQuant

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### MIT License: jev-trade

From `aowang-ai/jev-trade`.

```text
MIT License

Copyright (c) 2026 aowang

This repository is based on jev-trader by Jarrod Watts
(https://github.com/jarrodwatts/jev-trader), used under the MIT License.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### MIT License: jev-trader (upstream of jev-trade)

From `jarrodwatts/jev-trader@b587759`.

```text
MIT License

Copyright (c) 2026 Jarrod Watts

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
