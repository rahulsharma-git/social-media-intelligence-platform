# Build 5.13.1 fixes

The screenshot's Node.js 20 message is a warning, not the actual exit-code-1 error.
This build upgrades the JavaScript actions and adds a dedicated configuration check.

If the workflow fails again:
1. Open Actions > failed run > monitor.
2. Open the first red step.
3. The step now prints a specific missing-secret/network/Python error.
4. Download the `smi-report-...` artifact for `background_run_summary.json`
   and `github_step_summary.md` when available.

News defaults to the past 24 hours in both dashboard and background collection.
