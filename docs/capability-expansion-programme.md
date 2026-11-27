# Capability expansion programme: GitHub verification

Verified on 25 November 2026 before new feature implementation. Source baseline: `1317a16`; integration flow: feature ? develop ? main.

[Project board](https://github.com/users/mattordev/projects/1) ? [Capability audit](capability-expansion-audit.md)

Read-back verification confirmed four open undated milestones, four epics, 43 native parent?child relationships, 109 native dependency relationships and 47 project cards. Unrelated project cards were compared with the initial snapshot and were unchanged. Existing future milestones v1.6.0 and v1.6.5 remain separate.

Existing cards #43, #231 and #232 were reconciled rather than duplicated. #230 remains separate SearXNG lifecycle discovery; no managed service deployment is included.

## Milestones and epics

| Release | Epic | Child cards |
| --- | --- | --- |
| [v1.5.6 - Filesystem Capabilities](https://github.com/mattordev/coda/milestone/17) | [#235: Filesystem Capability Expansion](https://github.com/mattordev/coda/issues/235) | 14 |
| [v1.5.7 - Web Capabilities](https://github.com/mattordev/coda/milestone/18) | [#236: Web Capability Expansion](https://github.com/mattordev/coda/issues/236) | 10 |
| [v1.5.8 - System Execution and Scheduled Tasks](https://github.com/mattordev/coda/milestone/19) | [#237: System Execution and Scheduled Tasks](https://github.com/mattordev/coda/issues/237) | 11 |
| [v1.5.9 - Tool Intelligence and Composition](https://github.com/mattordev/coda/milestone/20) | [#238: Tool Intelligence and Composition](https://github.com/mattordev/coda/issues/238) | 8 |

## Verified child cards and dependencies

Every dependency below was retrieved from GitHub?s native blocked-by relationships, and every child?s native parent and milestone were checked. No text-only hierarchy is being substituted for those relationships.

### v1.5.6

| Child issue | Blocked by |
| --- | --- |
| [#239: plan(filesystem): define capability permissions and workspace safety](https://github.com/mattordev/coda/issues/239) | Existing released foundations |
| [#43: Create files and folders](https://github.com/mattordev/coda/issues/43) | [#239](https://github.com/mattordev/coda/issues/239) |
| [#240: feat(filesystem): add bounded partial file reads](https://github.com/mattordev/coda/issues/240) | [#239](https://github.com/mattordev/coda/issues/239) |
| [#241: feat(filesystem): expose authorised file metadata](https://github.com/mattordev/coda/issues/241) | [#239](https://github.com/mattordev/coda/issues/239) |
| [#242: feat(filesystem): search filenames within bounded workspaces](https://github.com/mattordev/coda/issues/242) | [#239](https://github.com/mattordev/coda/issues/239) |
| [#243: feat(filesystem): search bounded file content](https://github.com/mattordev/coda/issues/243) | [#242](https://github.com/mattordev/coda/issues/242) |
| [#244: feat(filesystem): edit and append authorised text files safely](https://github.com/mattordev/coda/issues/244) | [#239](https://github.com/mattordev/coda/issues/239), [#43](https://github.com/mattordev/coda/issues/43) |
| [#245: feat(filesystem): copy authorised files and directory trees](https://github.com/mattordev/coda/issues/245) | [#239](https://github.com/mattordev/coda/issues/239), [#43](https://github.com/mattordev/coda/issues/43) |
| [#246: feat(filesystem): move and rename authorised paths](https://github.com/mattordev/coda/issues/246) | [#239](https://github.com/mattordev/coda/issues/239), [#43](https://github.com/mattordev/coda/issues/43) |
| [#247: feat(filesystem): add explicitly approved destructive operations](https://github.com/mattordev/coda/issues/247) | [#239](https://github.com/mattordev/coda/issues/239) |
| [#232: enhancement(mcp): natural directory overviews and contextual follow-ups](https://github.com/mattordev/coda/issues/232) | [#242](https://github.com/mattordev/coda/issues/242), [#241](https://github.com/mattordev/coda/issues/241) |
| [#267: test(fs): validate v1.5.6 capabilities and security](https://github.com/mattordev/coda/issues/267) | [#239](https://github.com/mattordev/coda/issues/239), [#43](https://github.com/mattordev/coda/issues/43), [#240](https://github.com/mattordev/coda/issues/240), [#241](https://github.com/mattordev/coda/issues/241), [#242](https://github.com/mattordev/coda/issues/242), [#243](https://github.com/mattordev/coda/issues/243), [#244](https://github.com/mattordev/coda/issues/244), [#245](https://github.com/mattordev/coda/issues/245), [#246](https://github.com/mattordev/coda/issues/246), [#247](https://github.com/mattordev/coda/issues/247), [#232](https://github.com/mattordev/coda/issues/232) |
| [#268: docs(fs): document v1.5.6 setup permissions and migration](https://github.com/mattordev/coda/issues/268) | [#239](https://github.com/mattordev/coda/issues/239), [#43](https://github.com/mattordev/coda/issues/43), [#240](https://github.com/mattordev/coda/issues/240), [#241](https://github.com/mattordev/coda/issues/241), [#242](https://github.com/mattordev/coda/issues/242), [#243](https://github.com/mattordev/coda/issues/243), [#244](https://github.com/mattordev/coda/issues/244), [#245](https://github.com/mattordev/coda/issues/245), [#246](https://github.com/mattordev/coda/issues/246), [#247](https://github.com/mattordev/coda/issues/247), [#232](https://github.com/mattordev/coda/issues/232) |
| [#269: chore(release): validate and deliver v1.5.6](https://github.com/mattordev/coda/issues/269) | [#267](https://github.com/mattordev/coda/issues/267), [#268](https://github.com/mattordev/coda/issues/268) |

### v1.5.7

| Child issue | Blocked by |
| --- | --- |
| [#231: plan(websearch): choose how CODA selects and reads search result pages](https://github.com/mattordev/coda/issues/231) | Existing released foundations |
| [#248: feat(web): integrate bounded HTTP page retrieval and extraction](https://github.com/mattordev/coda/issues/248) | [#231](https://github.com/mattordev/coda/issues/231), [#249](https://github.com/mattordev/coda/issues/249) |
| [#249: feat(web): enforce retrieval network and untrusted-content boundaries](https://github.com/mattordev/coda/issues/249) | [#231](https://github.com/mattordev/coda/issues/231) |
| [#250: plan(browser): evaluate isolated Playwright MCP integration](https://github.com/mattordev/coda/issues/250) | [#231](https://github.com/mattordev/coda/issues/231) |
| [#251: feat(browser): add optional navigation inspection and capture](https://github.com/mattordev/coda/issues/251) | [#250](https://github.com/mattordev/coda/issues/250), [#249](https://github.com/mattordev/coda/issues/249) |
| [#252: feat(browser): enforce approvals for interactive external actions](https://github.com/mattordev/coda/issues/252) | [#251](https://github.com/mattordev/coda/issues/251) |
| [#253: feat(web): read selected sources through existing conversation runtime](https://github.com/mattordev/coda/issues/253) | [#248](https://github.com/mattordev/coda/issues/248) |
| [#270: test(web): validate v1.5.7 capabilities and security](https://github.com/mattordev/coda/issues/270) | [#231](https://github.com/mattordev/coda/issues/231), [#248](https://github.com/mattordev/coda/issues/248), [#249](https://github.com/mattordev/coda/issues/249), [#250](https://github.com/mattordev/coda/issues/250), [#251](https://github.com/mattordev/coda/issues/251), [#252](https://github.com/mattordev/coda/issues/252), [#253](https://github.com/mattordev/coda/issues/253) |
| [#271: docs(web): document v1.5.7 setup permissions and migration](https://github.com/mattordev/coda/issues/271) | [#231](https://github.com/mattordev/coda/issues/231), [#248](https://github.com/mattordev/coda/issues/248), [#249](https://github.com/mattordev/coda/issues/249), [#250](https://github.com/mattordev/coda/issues/250), [#251](https://github.com/mattordev/coda/issues/251), [#252](https://github.com/mattordev/coda/issues/252), [#253](https://github.com/mattordev/coda/issues/253) |
| [#272: chore(release): validate and deliver v1.5.7](https://github.com/mattordev/coda/issues/272) | [#270](https://github.com/mattordev/coda/issues/270), [#271](https://github.com/mattordev/coda/issues/271), [#269](https://github.com/mattordev/coda/issues/269) |

### v1.5.8

| Child issue | Blocked by |
| --- | --- |
| [#254: plan(execution): define approved commands and scheduled authority](https://github.com/mattordev/coda/issues/254) | Existing released foundations |
| [#255: feat(execution): run approved commands with bounded output](https://github.com/mattordev/coda/issues/255) | [#254](https://github.com/mattordev/coda/issues/254) |
| [#256: feat(execution): manage authorised background processes](https://github.com/mattordev/coda/issues/256) | [#255](https://github.com/mattordev/coda/issues/255) |
| [#257: feat(status): extend native resource and process information](https://github.com/mattordev/coda/issues/257) | [#254](https://github.com/mattordev/coda/issues/254) |
| [#258: feat(scheduler): persist schedules and execution history](https://github.com/mattordev/coda/issues/258) | [#254](https://github.com/mattordev/coda/issues/254) |
| [#259: feat(scheduler): manage one-time and recurring tasks](https://github.com/mattordev/coda/issues/259) | [#258](https://github.com/mattordev/coda/issues/258) |
| [#260: feat(scheduler): execute durable tasks with current permissions](https://github.com/mattordev/coda/issues/260) | [#259](https://github.com/mattordev/coda/issues/259), [#255](https://github.com/mattordev/coda/issues/255) |
| [#261: feat(scheduler): surface task completion and failures](https://github.com/mattordev/coda/issues/261) | [#260](https://github.com/mattordev/coda/issues/260), [#256](https://github.com/mattordev/coda/issues/256) |
| [#273: test(system): validate v1.5.8 capabilities and security](https://github.com/mattordev/coda/issues/273) | [#254](https://github.com/mattordev/coda/issues/254), [#255](https://github.com/mattordev/coda/issues/255), [#256](https://github.com/mattordev/coda/issues/256), [#257](https://github.com/mattordev/coda/issues/257), [#258](https://github.com/mattordev/coda/issues/258), [#259](https://github.com/mattordev/coda/issues/259), [#260](https://github.com/mattordev/coda/issues/260), [#261](https://github.com/mattordev/coda/issues/261) |
| [#274: docs(system): document v1.5.8 setup permissions and migration](https://github.com/mattordev/coda/issues/274) | [#254](https://github.com/mattordev/coda/issues/254), [#255](https://github.com/mattordev/coda/issues/255), [#256](https://github.com/mattordev/coda/issues/256), [#257](https://github.com/mattordev/coda/issues/257), [#258](https://github.com/mattordev/coda/issues/258), [#259](https://github.com/mattordev/coda/issues/259), [#260](https://github.com/mattordev/coda/issues/260), [#261](https://github.com/mattordev/coda/issues/261) |
| [#275: chore(release): validate and deliver v1.5.8](https://github.com/mattordev/coda/issues/275) | [#273](https://github.com/mattordev/coda/issues/273), [#274](https://github.com/mattordev/coda/issues/274), [#272](https://github.com/mattordev/coda/issues/272) |

### v1.5.9

| Child issue | Blocked by |
| --- | --- |
| [#262: feat(tools): expose a lightweight discoverable capability catalogue](https://github.com/mattordev/coda/issues/262) | Existing released foundations |
| [#263: feat(tools): select relevant tools with deferred schemas](https://github.com/mattordev/coda/issues/263) | [#262](https://github.com/mattordev/coda/issues/262) |
| [#264: feat(tools): execute dependent tool calls through the common dispatcher](https://github.com/mattordev/coda/issues/264) | [#263](https://github.com/mattordev/coda/issues/263), [#265](https://github.com/mattordev/coda/issues/265) |
| [#265: feat(tools): bound composed execution resources and retries](https://github.com/mattordev/coda/issues/265) | [#262](https://github.com/mattordev/coda/issues/262) |
| [#266: feat(tools): report composed progress clarification and partial outcomes](https://github.com/mattordev/coda/issues/266) | [#264](https://github.com/mattordev/coda/issues/264) |
| [#276: test(tools): validate v1.5.9 capabilities and security](https://github.com/mattordev/coda/issues/276) | [#262](https://github.com/mattordev/coda/issues/262), [#263](https://github.com/mattordev/coda/issues/263), [#264](https://github.com/mattordev/coda/issues/264), [#265](https://github.com/mattordev/coda/issues/265), [#266](https://github.com/mattordev/coda/issues/266) |
| [#277: docs(tools): document v1.5.9 setup permissions and migration](https://github.com/mattordev/coda/issues/277) | [#262](https://github.com/mattordev/coda/issues/262), [#263](https://github.com/mattordev/coda/issues/263), [#264](https://github.com/mattordev/coda/issues/264), [#265](https://github.com/mattordev/coda/issues/265), [#266](https://github.com/mattordev/coda/issues/266) |
| [#278: chore(release): validate and deliver v1.5.9](https://github.com/mattordev/coda/issues/278) | [#276](https://github.com/mattordev/coda/issues/276), [#277](https://github.com/mattordev/coda/issues/277), [#275](https://github.com/mattordev/coda/issues/275) |

## Project fields and lifecycle

Milestone and existing labels are assigned to every programme issue. Parent issue and sub-issue progress derive from native relationships. At verification #43 retained In Progress, #239 was Next and the remaining programme cards were Todo. Development and review activity must update those statuses after this initial snapshot.

No priority, release or iteration custom field exists. No new field or issue type was invented: dependencies and Next express execution order, and existing milestones identify releases. Existing personal-repository issue examples have no issue type.

Use In Progress while implementing or reviewing. Use Issues only for an actual documented blocker, with native blocking links where appropriate. Move to Done only after accepted integration and satisfied criteria. Open PRs alone do not complete issues or releases.

All project-management operations above succeeded and were independently retrieved. No permission failure remains outstanding. A 404 from the personal-user issue-types endpoint is not treated as an available classification system.

## Continuation

Release workflow: keep each release's feature work on one feature branch, open
its integration PR into `develop`, then promote the accepted release with a
separate `develop` → `main` PR. Create the release tag and published artifacts
from the accepted main commit. An integrated feature PR does not itself complete
the release milestone. This is the user-confirmed workflow for subsequent work.

Start with [#239](https://github.com/mattordev/coda/issues/239), the capability audit and permission boundaries, and the preserved [#43](https://github.com/mattordev/coda/issues/43) creation draft. New bounded reading is [#240](https://github.com/mattordev/coda/issues/240). Each release?s tests, documentation and release card remain required; do not publish an incomplete milestone.

The current audit/verification PR is [#279](https://github.com/mattordev/coda/pull/279). Check current issue comments, linked PRs and board state before resuming; this document records establishment rather than claiming delivery.
