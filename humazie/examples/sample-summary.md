# Humazie Review Report

## Review Summary

| Field | Value |
| --- | --- |
| Run ID | run-2026-07-23T16-19-21-437Z-8c7e78 |
| Git commit | 9d245c5 |
| Environment | humazie-harness |
| Base URL | http://127.0.0.1:5173/humazie.html |
| Mobile | false |
| Auto-fix | false |
| Routes / modes reviewed | #explore, #focus, #views, #command |
| Total flows | 10 |
| Passed | 10 |
| Failed | 0 |
| Issues found | 0 |
| Issues fixed | 0 |
| Manual review | 0 |
| Duration | 17s |

## User Journeys Tested

### Application loads successfully

- **Goal:** Confirm Strata shell connects via the harness and shows the command bar.
- **Actions:** Open harness → Brand is visible → Mode navigation is present
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_1c777d1d0c_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_1c777d1d0c_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_1c777d1d0c_2.png`

### Main mode navigation works

- **Goal:** Switch between Focus, Explore, Views, and Command without losing the shell.
- **Actions:** Open harness → Enter Focus mode → Focus remains pressed → Enter Explore mode → Enter Views mode → Enter Command mode → Shell still present
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_e7c0e05a26_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_e7c0e05a26_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_e7c0e05a26_2.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_e7c0e05a26_3.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_e7c0e05a26_4.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_e7c0e05a26_5.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_e7c0e05a26_6.png`

### Capture text into the Inbox

- **Goal:** Open Capture, enter a note with a reason, submit it, and verify the dialog closes.
- **Actions:** Open harness → Open Capture dialog → Capture dialog opens → Enter capture content → Enter capture reason → Submit capture → Dialog dismisses after success
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_bec1a0194d_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_bec1a0194d_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_bec1a0194d_2.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_bec1a0194d_3.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_bec1a0194d_4.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_bec1a0194d_5.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_bec1a0194d_6.png`

### Capture rejects empty content

- **Goal:** Confirm the Capture submit control stays disabled with nothing to send.
- **Actions:** Open harness → Open Capture → Dialog open → Assert primary Capture submit is disabled when empty
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_9629abc768_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_9629abc768_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_9629abc768_2.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_9629abc768_3.png`

### Health dialog opens and dismisses

- **Goal:** Open the knowledge health dialog and close it again.
- **Actions:** Open harness → Open Health → Health dialog visible → Close Health dialog → Health dialog dismissed
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_2438fa445e_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_2438fa445e_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_2438fa445e_2.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_2438fa445e_3.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_2438fa445e_4.png`

### Navigator drawer toggles

- **Goal:** Collapse and expand the navigator without breaking the workspace.
- **Actions:** Open harness → Toggle navigator → Shell remains → Toggle navigator back
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_20ba617c97_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_20ba617c97_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_20ba617c97_2.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_20ba617c97_3.png`

### Switch graph dimension between 2D and 3D

- **Goal:** Toggle the graph dimension controls and keep the shell responsive.
- **Actions:** Open harness → Select Explore → Choose 2D → Choose 3D → Brand still visible
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_1da8ed42ff_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_1da8ed42ff_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_1da8ed42ff_2.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_1da8ed42ff_3.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_1da8ed42ff_4.png`

### Inspector tabs switch panels

- **Goal:** Move between AI, Changes, Properties, and Links inspector tabs.
- **Actions:** Open harness → Open AI tab → Open Changes tab → Open Properties tab → Open Links tab
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_06f71d90f3_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_06f71d90f3_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_06f71d90f3_2.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_06f71d90f3_3.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_06f71d90f3_4.png`

### Important controls expose accessible names

- **Goal:** Verify primary controls are reachable by accessible name for keyboard users.
- **Actions:** Open harness → Capture has accessible name → Health has accessible name → Run accessibility scan on shell
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_54ad71e5d9_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_54ad71e5d9_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_54ad71e5d9_2.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_54ad71e5d9_3.png`

### Browser console stays clean on load

- **Goal:** Load the harness and confirm no unexpected console errors appear.
- **Actions:** Open harness → Shell loaded → Assert no unexpected console errors were collected
- **Result:** passed
- **Evidence:** `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_d8939745c5_0.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_d8939745c5_1.png`, `F:\01.projects\strata\.humazie\runs\run-2026-07-23T16-19-21-437Z-8c7e78\screenshots\flow_d8939745c5_2.png`

## Issues

_No issues discovered._

## Automatic Fixes

_No automatic fixes attempted._

## Product Map Snapshot

Nodes: 26; Edges: 18; Modes: focus, explore, views, command

## Remaining Risks

- Remote AI provider calls are excluded (unsafeActions).
- Encryption key wipe / recovery flows require manual fixtures.
- Qt WebEngine desktop shell e2e remains covered by pytest, not Humazie.

_Generated by Humazie Bot_
