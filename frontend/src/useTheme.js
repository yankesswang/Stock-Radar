import { useEffect, useState } from 'react';
import Theme from './theme';

/** { pref, actual, set, cycle } —— pref 是使用者偏好（含 system），actual 是實際套用的 light/dark */
export function useTheme() {
  const [state, setState] = useState(() => ({ pref: Theme.get(), actual: Theme.resolved() }));
  useEffect(() => Theme.subscribe(({ pref, actual }) => setState({ pref, actual })), []);
  return { ...state, set: Theme.set, cycle: Theme.cycle, label: Theme.label(state.pref) };
}
