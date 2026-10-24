// Human-readable names for The Virtual Brain's 76-region (Kötter–Wanke) map.
// Region ids are "l"/"r" + abbreviation. Names are approximate, for display only.
const ABBR = {
  A1: 'Primary auditory', A2: 'Secondary auditory', AMYG: 'Amygdala',
  CCA: 'Anterior cingulate', CCP: 'Posterior cingulate', CCR: 'Retrosplenial cingulate',
  CCS: 'Subgenual cingulate', CC: 'Cingulate', FEF: 'Frontal eye field', G: 'Gustatory',
  HC: 'Hippocampus', IA: 'Anterior insula', IP: 'Inferior parietal', M1: 'Primary motor',
  PCI: 'Inferior parietal', PCIP: 'Intraparietal', PCM: 'Medial parietal', PCS: 'Superior parietal',
  PFCCL: 'Centrolateral prefrontal', PFCDL: 'Dorsolateral prefrontal (planning)',
  PFCDM: 'Dorsomedial prefrontal', PFCM: 'Medial prefrontal', PFCORB: 'Orbital prefrontal',
  PFCPOL: 'Frontal pole', PFCVL: 'Ventrolateral prefrontal', PHC: 'Parahippocampal',
  PMCDL: 'Dorsal premotor', PMCM: 'Supplementary motor', PMCVL: 'Ventral premotor',
  S1: 'Primary sensory', S2: 'Secondary sensory', TCC: 'Central temporal',
  TCI: 'Inferior temporal', TCPOL: 'Temporal pole', TCS: 'Superior temporal',
  TCV: 'Ventral temporal', V1: 'Primary visual', V2: 'Secondary visual',
};

const SHORT = {
  M1: 'Motor', PMCDL: 'Premotor', PMCVL: 'Premotor (v)', PMCM: 'SMA',
  PFCDL: 'Planning', S1: 'Sensory', HC: 'Hippocampus',
};

export function regionName(id) {
  if (!id) return '';
  const side = id[0] === 'l' ? 'Left' : id[0] === 'r' ? 'Right' : '';
  return `${side} ${ABBR[id.slice(1)] || id.slice(1)}`.trim();
}

export function regionShort(id) {
  if (!id) return '';
  return `${id[0].toUpperCase()} ${SHORT[id.slice(1)] || id.slice(1)}`;
}

// Regions labelled on the 3D brain at all times
export const KEY_REGIONS = ['lM1', 'rM1', 'lPMCDL', 'lPMCM', 'lPFCDL', 'lS1'];
