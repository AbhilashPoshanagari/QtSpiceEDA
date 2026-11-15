# Library folder

Put your SPICE model files (`.lib`, `.sub`, `.mod`, ...) and LTspice symbols (`.asy`)
here. Subfolders are fine.

QtSpice searches this folder automatically:

- symbols here appear in the schematic editor's symbol list;
- models and subcircuits here appear in the library browser;
- a netlist can include a file by name only, with no path:

      .include tsmc018.lib
      .lib corners.lib tt

How an include file is found: an absolute path is used as written; a relative name is
looked up next to the netlist first, then here, then in the other library folders (the
library browser's "Search paths…" button). In each folder the file is looked for
directly, then in subfolders.

This is the default folder when QtSpice runs from source. The packaged app uses
`Documents/QtSpice/library` instead. You can choose another folder in the library
browser's "Search paths…" dialog.

Files you put here are not committed to git (see `.gitignore`): vendor model files
often have their own license.
