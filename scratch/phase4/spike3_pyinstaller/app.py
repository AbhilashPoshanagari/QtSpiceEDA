import sys

from dss import DSS

ctx = DSS.NewContext()
ctx.AllowChangeDir = False
ctx.Text.Command = f'compile "{sys.argv[1]}"'
ctx.ActiveCircuit.SetActiveBus("671")
v = ctx.ActiveCircuit.ActiveBus.puVmagAngle
print(f"converged={ctx.ActiveCircuit.Solution.Converged} 671.1={v[0]:.6f} pu")
