# Current GPT models in Xcode’s built-in Codex integration

This fork adds a reversible installer and a verified update for **Xcode 27.1 (27A9275), Apple Silicon and Codex 0.165.0**. It keeps the existing ChatGPT account authentication; no API key is required.

The repository name comes from the original Xcode 26.6 guide. Its investigation is preserved in [Xcode-26.6-Codex-Investigation.md](Xcode-26.6-Codex-Investigation.md). Earlier account-handshake failures on Xcode 26.6 do not establish compatibility with newer Xcode versions.

This changes Xcode’s per-user agent runtime. It is an unofficial configuration, and future Xcode updates may replace the build-specific runtime link.

## Verified on 28 October 2026

| Check | Result |
|---|---|
| Xcode | 27.1, build 27A9275 |
| Runtime upgrade | Codex 0.153.1 → 0.165.0 |
| Download integrity | SHA256 matched GitHub release asset digest and published SHA256SUMS |
| ChatGPT authentication | Successful |
| Default model | `gpt-6.1-sol` |
| Conversation | Sol completed successfully at `low` reasoning |
| Native Xcode tools | `XcodeLS` identified the open project and listed its immediate groups |
| Model picker | GPT-6.1 Sol, GPT-6.1 Terra, GPT-6 Astra, GPT-6 Sol and GPT-6 Luna visible |

Only Sol was exercised in a conversation. The other models were verified in the picker and refreshed catalog; their execution and higher reasoning levels were not tested. Availability depends on your account, workspace policy and rollout access. See [OpenAI’s current model documentation](https://learn.chatgpt.com/docs/models?surface=web).

## Install

Requirements: macOS on Apple Silicon, Python 3.11 or newer, full Xcode selected with `xcode-select`, and an existing Codex setup in Xcode. The script uses the Python standard library and does not need npm or the terminal Codex installation.

Clone this fork, enter its directory, then inspect the proposed changes:

```bash
git clone https://github.com/Legitdove/how-to-use-gpt5.6-with-xcode-26.6.git
cd how-to-use-gpt5.6-with-xcode-26.6
python3 scripts/xcode_codex.py
```

The default command is a dry run: it prints the selected Xcode build, current runtime, proposed runtime and model. It makes no changes or network requests.

Quit Xcode normally, resolving any save or running-task prompts, then install:

```bash
python3 scripts/xcode_codex.py --apply
```

The script:

1. Determines the selected Xcode build instead of hardcoding a build number.
2. Downloads the official [Codex 0.165.0 package](https://github.com/openai/codex/releases/tag/rust-v0.165.0) and checks its SHA256 against both published sources.
3. Installs the full package in a separate version directory, retaining `bin/codex`, its matching `codex-code-mode-host`, resources and path helpers.
4. Preserves the current Xcode package metadata, updating the version, download URL, archive SHA512 and `bin/codex` entrypoint.
5. Creates a private, timestamped backup of the runtime link target, configuration and model cache.
6. Changes only the top-level model to `gpt-6.1-sol`, removes the backed-up cache for refresh, and switches only the selected build’s runtime link.

It refuses to overwrite an existing version directory or switch while Xcode is running. An installation error during the configuration/link change triggers restoration of the previous state. It does not modify Xcode.app, credentials, unrelated build links, reasoning settings or service-tier settings.

Reopen Xcode manually and complete the checks below. Installing a package alone does not prove Xcode protocol compatibility.

### Explicit version and model

Defaults are pinned to the verified version rather than automatically following new releases. To test another stable release or model:

```bash
python3 scripts/xcode_codex.py --apply --version 0.165.0 --model gpt-6.1-sol
```

Use a canonical model identifier, **not a display label**. For example, `GPT 6.1-Sol` caused a model-not-supported error; `gpt-6.1-sol` worked. Do not assume a newer release will work merely because it installs successfully.

## Verify in Xcode

1. Open your project and start a **new Codex conversation**. Confirm account initialization completes.
2. Open Conversation Settings → Model. Check that the expected models appear.
3. Submit this read-only prompt:

   > Runtime verification only. Do not edit any files. Use only Xcode-native tools to identify the currently open project's name and list its top-level project files. Do not use shell commands or direct filesystem inspection. Briefly report which Xcode tools you used.

4. Confirm a successful response uses `XcodeLS` and matches the open project. The verified run completed in 22 seconds.

The model cache at `~/Library/Developer/Xcode/CodingAssistant/codex/models_cache.json` should show client version `0.165.0` and the current catalog. Xcode’s Codex configuration is in the same directory’s `config.toml`. Logs are in `logs_2.sqlite`; inspect only the request metadata needed to verify the model and authentication, since full log entries can include private project content.

A generic “newer version available” banner appeared once in the verified setup and was dismissed. The runtime executable and refreshed cache confirmed 0.165.0 was active.

If account initialization hangs, the conversation fails, or native tools fail, roll back. Do not delete authentication data or change credentials to work around a runtime incompatibility.

## Rollback

The installer prints the backup directory, normally under:

```text
~/Library/Developer/Xcode/CodingAssistant/upgrade-backups/<timestamp>-<unique-suffix>
```

Quit Xcode normally, then use the exact backup path printed by your installation:

```bash
python3 scripts/xcode_codex.py --apply --rollback "/absolute/path/to/your/backup"
```

Reopen Xcode. Rollback restores the previous runtime link, exact configuration and model cache. This also restores any incorrect model identifier that existed before the upgrade. Both runtime versions remain on disk.

Backups can contain private configuration. Keep them local; do not commit or publish them.

After an Xcode update, rerun the dry run to inspect its new build-specific link. This script deliberately does not alter all installed Xcode versions.

## Development

```bash
python3 -m unittest discover -s tests -v
```

Tests cover dry-run behavior, configuration preservation, checksum failures, unsafe archives, exact rollback, absent files and automatic recovery from a failed switch. They use temporary directories and do not modify your Xcode installation.

## Sources

- [Original guide](https://github.com/acceleroto/how-to-use-gpt5.6-with-xcode-26.6)
- [Official Codex 0.165.0 release and assets](https://github.com/openai/codex/releases/tag/rust-v0.165.0)
- [Current OpenAI model documentation](https://learn.chatgpt.com/docs/models?surface=web)
