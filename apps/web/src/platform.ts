export interface PlatformSignals {
  userAgent: string;
  platform: string;
  maxTouchPoints: number;
  viewportWidth: number;
}

export function canAddBooks(signals: PlatformSignals): boolean {
  const mac = /Mac/i.test(`${signals.platform} ${signals.userAgent}`);
  const iPadPretendingToBeMac = signals.maxTouchPoints > 1;
  return mac && !iPadPretendingToBeMac && signals.viewportWidth >= 768;
}

export function currentPlatformSignals(): PlatformSignals {
  return {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    maxTouchPoints: navigator.maxTouchPoints,
    viewportWidth: window.innerWidth,
  };
}
