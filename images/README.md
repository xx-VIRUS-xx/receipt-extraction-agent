# Receipt images go here

The 45 receipt images used for the evaluation came with the exercise and are **not redistributed** in this repository
(they are real receipts, and they are not mine to republish).

To run the agent, put your own receipt images (`.jpg`, `.jpeg`, `.png`, `.webp`) in this folder, or point at another
folder with `--images /path/to/folder`.

`eval/labels.json` holds the labels for the original 45 files, keyed by file name. It is only useful if you have
those same files; to evaluate on your own receipts, write a labels file in the same format
(see `eval/labels_part1.py` for the fields).
