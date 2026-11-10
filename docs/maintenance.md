# Maintaining the public showcase

This repository contains reviewed public snapshots. Keep using this same repository for future releases so its history shows the project's progress.

1. Finish and test a meaningful change in the development source.
2. Generate a separate candidate from the explicitly approved file list. Exclude internal documents, deployment manifests, credentials, recordings and real client profiles. New source files require review before joining the list.
3. Reapply the public examples and documentation. Keep browser configuration and voice IDs as placeholders. Do not copy `.git`, environment files or source history into the candidate.
4. Compare the candidate with this repository. Review changed content, filenames, test fixtures, comments and images for private information. Run a secret scanner as an additional check.
5. Run the README's portal, mock-server, database and agent tests. Check the local demonstration after visible changes.
6. Update `CHANGELOG.md` and the README's status, then commit and push the reviewed changes normally. Do not replace or force-push the public history.

Keep the exact private export mapping outside this repository. Avoid automatic mirroring of a private repository: adding a new private file must never publish it implicitly.

The public GitHub workflow only runs tests. It does not deploy an agent, publish a website, run migrations against a hosted database, or use production secrets.
