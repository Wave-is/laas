# Release procedure

Version source: `src/version.py`. Bump both the semantic version and the monotonically
increasing four-part Windows version. Config schema versions are separate.
Keep the installer AppId and application executable name stable for upgrades.

1. Update CHANGELOG and all three README files. Review the validation and known limits.
   When changing the logo, run `python tools/build_brand_assets.py` and commit the
   resulting assets. Release builds consume those committed assets without regenerating them.
2. Use a clean Git checkout and Python 3.11–3.13 x64 on Windows. Install requirements-dev.
3. Install [Inno Setup 6.7.3](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3).
   Official installer SHA256: `9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732`.
4. Commit all release inputs, then run `build_installer.ps1 -Python <python.exe> -ISCC <ISCC.exe>`.
   Tests, compiled C# checks, PyInstaller, dependency notices and Inno compilation must pass.
   `-SkipBuild` is only for repackaging the same verified binary from the same commit.
5. Test installation, repair/reinstall, removal and data preservation as a normal user.
   Confirm Start/Desktop shortcuts, Apps uninstall entry, default no-autostart, the
   running-app guard, and removal of an owned Startup shortcut. Check all three Setup languages.
6. Review `dist/release/BUILD.json`, source archive and SHA256SUMS.txt. The archive is
   created from HEAD, not the working folder. Never include runtime/handoff-local data.
7. Push only the reviewed branch and intended version tag. Keep repository visibility
   unchanged. Create a **prerelease**, with three-language notes and all files from
   `dist/release/`. Download the uploaded assets and compare their hashes.
8. Verify GitHub Actions. Add only evidence actually collected to the release notes.
   Update HANDOFF, WORKLOG and private machine notes; run `tools/handoff_snapshot.py`.

The workflow builds an installer artifact; it does not automatically publish or change
repository visibility. No code-signing key is configured. Native binary builds are
not promised to be byte-for-byte reproducible; BUILD.json records source and tool versions.

Local historical development branches may contain private context. The first shared
`main` is an audited root snapshot. Do not push historical branches or `git push --all`.

Upgrades preserve AppData. Uninstall removes an owned current-user Startup shortcut
only when its target matches the installed EXE. No agents, drivers or SYSTEM services
are installed by Setup. Installed GPU-helper maintenance is a separate admin operation.
