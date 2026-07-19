export interface EpochRef {
  current: number;
}

export function beginEpoch(epoch: EpochRef): number {
  epoch.current += 1;
  return epoch.current;
}

export function invalidateEpoch(epoch: EpochRef): number {
  epoch.current += 1;
  return epoch.current;
}

export function isCurrentEpoch(epoch: EpochRef, candidate: number): boolean {
  return epoch.current === candidate;
}
