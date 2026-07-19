export interface ResetGateRef {
  current: boolean;
}

export function tryBeginReset(gate: ResetGateRef): boolean {
  if (gate.current) return false;
  gate.current = true;
  return true;
}

export function finishReset(gate: ResetGateRef): void {
  gate.current = false;
}
