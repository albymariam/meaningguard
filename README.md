# MeaningGuard-Indic

Compiler-inspired verification of English to Hindi translation: facts (numbers, currency,
negation) and emotional tone are checked between source and translation.

## Setup (Windows with WSL2)

1. In an administrator PowerShell: `wsl --install`, then restart and open Ubuntu.
2. In Ubuntu, keep the repo in the Linux home folder (not under `/mnt/c`):

       cd ~
       unzip /mnt/c/Users/<you>/Downloads/meaningguard_phase0.zip
       cd meaningguard
       sudo apt update && sudo apt install -y python3.11 python3.11-venv git
       python3.11 -m venv .venv
       source .venv/bin/activate
       pip install -r requirements-dev.txt

   If `python3.11` is not available on your Ubuntu version, use the newest Python 3.10 to 3.13
   that is.
3. Edit with VS Code and its WSL extension (`code .` from the Ubuntu terminal).

On Linux or macOS, skip step 1.

## Hugging Face access

The IndicTrans2 models are gated. Accept the terms on both model pages
(`ai4bharat/indictrans2-en-indic-dist-200M`, `ai4bharat/indictrans2-indic-en-dist-200M`),
create a read token, and set it. Never commit it.

    export HF_TOKEN=hf_...

## Check everything

    python -m meaningguard.check_models            # environment, packages, model access
    python -m meaningguard.check_models --full     # loads every model, runs one inference
    python -m meaningguard.check_models --full --network   # also tests the Google fallback

## Tests

    pytest -q
    ruff check .
