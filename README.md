# Book Cleaner Agent

Python agent that reads files in `books`, strips pirate-site tags from filenames, and removes extra file metadata.

It removes these fragments from names:

- `z-library.sk`
- `1lib.sk`
- `z-lib.sk`

## Setup

```powershell
python -m pip install -r requirements.txt
```

## Usage

Clean the default `books` folder:

```powershell
python book_cleaner_agent.py
```

Preview changes without writing files:

```powershell
python book_cleaner_agent.py --dry-run
```

Clean another folder:

```powershell
python book_cleaner_agent.py "D:\path\to\books"
```

## What it does

- Renames files by removing `z-library.sk`, `1lib.sk`, and `z-lib.sk`, then leftover empty parentheses.
- Clears PDF document info metadata.
- Trims EPUB OPF metadata down to title, language, identifier, creator, and cover.
- Skips incomplete downloads (`.crdownload`, `.tmp`, `.part`, `.download`).

## Tests

```powershell
python -m unittest test_book_cleaner_agent.py
```

## Troubleshooting

- `Folder not found`: pass the full path to the books folder.
- A file is skipped as incomplete: wait for the download to finish, then run the agent again.
- Rename fails because the clean name already exists: the agent adds ` (2)`, ` (3)`, and so on.
# libros-limpiador, papus s-lib jaja
