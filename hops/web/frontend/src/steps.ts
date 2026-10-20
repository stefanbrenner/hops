import { StageKey } from './api'

export const STEPS: { key: StageKey; title: string; description: string; mandatory: boolean }[] = [
  { key: 'data_target', title: 'Data & target', description: 'Files, header keywords, target and location', mandatory: true },
  { key: 'reduction', title: 'Reduction', description: 'Bias, dark and flat correction', mandatory: true },
  { key: 'inspection', title: 'Inspection', description: 'Review frames and exclude bad ones', mandatory: false },
  { key: 'alignment', title: 'Alignment', description: 'Align all frames to the first one', mandatory: true },
  { key: 'photometry', title: 'Photometry', description: 'Choose target and comparison stars', mandatory: true },
  { key: 'fitting', title: 'Exoplanet fitting', description: 'Fit the transit light curve', mandatory: false },
]
