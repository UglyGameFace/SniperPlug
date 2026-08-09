# Active Task

## Status
In progress on branch `fix/dm-rules-optional-fields`. PR #224 is already merged into `main` and the live screenshot confirms the new DM rules modal is deployed, but its first version forces users to resubmit unrelated settings.

## Task
**SP-DM-UX-225 — Make the personal DM rules form partial and non-forcing**

## Scope
1. Keep all deal matching, exact proof, Smart-mode, public threshold, dedupe, and Flip Override behavior unchanged.
2. Let a user edit only the personal DM rule they actually want to change.
3. Make every DM rules modal field optional; blank must mean keep the current value.
4. Stop pre-filling all current values into the editable text boxes.
5. Show current values as placeholders/hints instead.
6. Keep maximum-price removal explicit so blank does not accidentally clear a limit.
7. Preserve the existing draft / Save changes / discard workflow and validate the full suite.

## Findings
- The deployed PR #224 modal marks threshold, savings, score, and daily cap as required and pre-populates all scalar values.
- That makes a single-setting edit feel like a full configuration rewrite even though most values are unrelated to the requested change.
- Maximum price also needs three distinct meanings: blank = keep current, numeric value = set/update limit, explicit `off` = remove limit.

## Changes
- All five personal DM rule fields are optional.
- Blank fields now mean `keep current value` and are ignored by the parser/update path.
- Current values are displayed through field placeholders instead of editable defaults.
- Maximum price accepts explicit `off`, `none`, `clear`, or `no limit` to remove an existing limit; blank keeps the current limit unchanged.
- Modal submission updates only fields that were actually supplied and changed.
- Submitting an entirely blank form leaves every current setting untouched and says so.
- The DM menu explains that the modal is partial-edit: fill only what you want to change.
- Focused regressions cover blank/no-op submission, threshold-only edits, explicit max-price removal, validation, and non-required modal fields.

## Validation
- Pending GitHub Actions validation after PR creation.

## Cleanup and conflict inspection
- No new preference storage or alternate save path was introduced.
- Existing `Save changes` remains the only persistence action from the visual menu.
- No matching or delivery policy changes are in scope.

## Current branch / PR
- Branch: `fix/dm-rules-optional-fields`
- PR: pending

## Deployment boundary
- Canonical Discloud app: `1779293887444`.
- Duplicate app `1785806676351` must never be targeted.
- Do not claim production completion until merged `main` is deployed to the canonical app and the live Discord form shows no forced fields.

## Backlog
- Investigate the high volume of Walmart `seller` identity blocks and transient exact-fetch failures as a separate exact-verification task.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into this task.
