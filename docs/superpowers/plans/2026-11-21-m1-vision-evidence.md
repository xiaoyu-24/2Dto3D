# M1 Vision Evidence Implementation Plan

**Goal** Continue the existing M12 parse task through page evidence, verified image input, and an immutable basis plus independent checklist. Record estimated sample dimensions as trial parameters.

**Architecture** The existing Worker consumes a `vision_read` stage on explicit request. The stage and existing content-addressed blobs hold the verified page and crops. The isolated M0 Harness service receives images for the approved project and records the provider response metadata. The Worker freezes the basis and checklist on successful variant validation.

**Stack** MySQL 8.4, existing FastAPI and React, Poppler, M0 Harness SDK/Runtime 0.2.2rc2 and Microsoft Edge.

- [x] Add the M12 page regions and independent variant check in `backend/app/m1_sample.py` and `backend/app/m1_acceptance.py`. Verify the coordinates against the stored page image and exercise both wrong lengths.
- [x] Reuse the parse input in the existing Worker, store page evidence under its lease, and expose it through project-authorized endpoints.
- [x] Connect the isolated M0 runtime, reserve project and task budget, store the passing synthetic challenge, and record the real page request. Verify resuming after missing configuration and reusing the completed task.
- [x] Verify automatic freezing after successful read, both download hashes, and rejection of mismatched readings in a temporary test database.
- [x] Use Microsoft Edge with channel `msedge` to reopen task #2, locate crops on the original page, and verify retry reuse in the independent Compose project.
- [x] Reuse the successful reading for task #2 and download both frozen M1.2 files in Edge. Verify file hashes, trial provenance, and unchanged provider calls.
- [x] Require two distinct and consistent end-view claims. Reject swapped, conflicting, or missing views. Retain prior files for audit and make downloads available after revalidation into M1.3.
