Version 4
SymbolType CELL
RECTANGLE Normal -48 -64 48 64
LINE Normal -80 -32 -48 -32
LINE Normal -80 32 -48 32
LINE Normal 48 -32 80 -32
LINE Normal 48 32 80 32
TEXT 0 0 Center 1 CPM
WINDOW 0 -48 -80 Left 2
WINDOW 3 -48 80 Left 2
SYMATTR Value CPM_modulator
SYMATTR Prefix X
SYMATTR Description Peak current mode modulator, 1 MHz (buck_cpm sample; parameters fixed in buck_cpm.lib)
SYMATTR ModelFile buck_cpm.lib
PIN -80 -32 LEFT 8
PINATTR PinName vc
PINATTR SpiceOrder 1
PIN -80 32 LEFT 8
PINATTR PinName vs
PINATTR SpiceOrder 2
PIN 80 -32 RIGHT 8
PINATTR PinName PWM
PINATTR SpiceOrder 3
PIN 80 32 RIGHT 8
PINATTR PinName ramp
PINATTR SpiceOrder 4
