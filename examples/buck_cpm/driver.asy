Version 4
SymbolType CELL
RECTANGLE Normal -48 -48 48 48
LINE Normal -80 -32 -48 -32
LINE Normal 0 -80 0 -48
LINE Normal 0 48 0 80
LINE Normal -80 32 -48 32
LINE Normal 48 0 80 0
TEXT 0 0 Center 1 DRV
WINDOW 0 -48 -64 Left 2
WINDOW 3 -48 64 Left 2
SYMATTR Value driver
SYMATTR Prefix X
SYMATTR Description Gate driver, Tdelay = 20 ns (buck_cpm sample)
SYMATTR ModelFile buck_cpm.lib
PIN -80 -32 LEFT 8
PINATTR PinName c
PINATTR SpiceOrder 1
PIN 0 -80 TOP 8
PINATTR PinName Vdd
PINATTR SpiceOrder 2
PIN 0 80 BOTTOM 8
PINATTR PinName Vss
PINATTR SpiceOrder 3
PIN -80 32 LEFT 8
PINATTR PinName Vssin
PINATTR SpiceOrder 4
PIN 80 0 RIGHT 8
PINATTR PinName out
PINATTR SpiceOrder 5
