import { MD3LightTheme } from 'react-native-paper';

export const palette = {
  primary: '#0E7490',
  blue: '#2563EB',
  teal: '#14B8A6',
  background: '#F8FAFC',
  surface: '#FFFFFF',
  ink: '#0F172A',
  muted: '#64748B',
  border: '#CBD5E1',
  fresh: '#16A34A',
  warning: '#D97706',
  danger: '#DC2626',
  freezer: '#4F46E5',
  pantry: '#B45309',
  fridge: '#0E7490',
} as const;

export const theme = {
  ...MD3LightTheme,
  roundness: 3,
  colors: {
    ...MD3LightTheme.colors,
    primary: palette.primary,
    secondary: palette.teal,
    background: palette.background,
    surface: palette.surface,
    surfaceVariant: '#EAF4F6',
    onSurface: palette.ink,
    onSurfaceVariant: palette.muted,
    outline: palette.border,
    error: palette.danger,
  },
};
