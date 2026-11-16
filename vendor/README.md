# Bundled libraries

Shipped with the program so it starts without downloading anything.

| Library | Version | Licence | Used for |
|---|---|---|---|
| `serial` (pySerial) | 3.5 | BSD-3-Clause, see `serial/LICENSE.txt` | talking to the reader's serial port |

The launchers put this folder on Python's search path. A pySerial installed with
`pip` takes the same name; whichever is found first is used.
