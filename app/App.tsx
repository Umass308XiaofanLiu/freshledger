import { useEffect, useMemo, useState } from 'react';
import {
  Image,
  type ImageSourcePropType,
  Platform,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import * as ImageManipulator from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';
import { StatusBar } from 'expo-status-bar';
import {
  ActivityIndicator,
  Appbar,
  Button,
  Card,
  Chip,
  Dialog,
  Divider,
  Menu,
  PaperProvider,
  Portal,
  ProgressBar,
  SegmentedButtons,
  Snackbar,
  Surface,
  Text,
  TextInput,
} from 'react-native-paper';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import {
  confirmReceipt,
  consumeMealItems,
  consumePantryItem,
  FreshLedgerApiError,
  getInsights,
  getMeals,
  getPantry,
  scanDemoReceipt,
  scanReceipt,
  seedDemo,
  spoilPantryItem,
} from './src/api';
import { applyIdentitySafety } from './src/reviewState';
import { palette, theme } from './src/theme';
import type {
  InsightsResponse,
  MealSuggestion,
  MealsResponse,
  PantryItem,
  PantryResponse,
  ReceiptDraft,
  ReceiptItem,
  StorageMethod,
} from './src/types';

type Mode = 'demo' | 'local' | 'gpt';
type ScanState = 'idle' | 'preparing' | 'reading' | 'done' | 'confirming' | 'confirmed' | 'error';
type ProvenanceKind = 'demo' | 'local' | 'openai' | 'unknown';

interface PreparedImage {
  uri: string;
  width: number;
  height: number;
}

interface SampleDefinition {
  id: string;
  title: string;
  subtitle: string;
  image: ImageSourcePropType;
}

const SAMPLES: SampleDefinition[] = [
  {
    id: 'r1',
    title: 'Fresh Basket',
    subtitle: 'Everyday essentials · 5 items',
    image: require('./assets/samples/r1.jpg'),
  },
  {
    id: 'r2',
    title: 'Neighborhood Grocer',
    subtitle: 'Coupons + weighted produce · 7 items',
    image: require('./assets/samples/r2.jpg'),
  },
  {
    id: 'r3',
    title: 'World Pantry',
    subtitle: 'Longer mixed-category receipt · 8 items',
    image: require('./assets/samples/r3.jpg'),
  },
];

const storagePresentation: Record<
  StorageMethod,
  { icon: string; color: string; background: string }
> = {
  fridge: { icon: 'fridge-outline', color: palette.fridge, background: '#CFFAFE' },
  freezer: { icon: 'snowflake', color: palette.freezer, background: '#E0E7FF' },
  pantry: { icon: 'cupboard-outline', color: palette.pantry, background: '#FEF3C7' },
};

const freshnessPresentation = {
  expired: { label: 'Past best-by', color: palette.danger, progress: 0.04 },
  urgent: { label: 'Use now', color: palette.danger, progress: 0.18 },
  soon: { label: 'Use soon', color: palette.warning, progress: 0.45 },
  fresh: { label: 'Fresh', color: palette.fresh, progress: 0.86 },
};

const mealBackgrounds = ['#ECFDF5', '#EFF6FF', '#FFF7ED'] as const;

const advicePresentation = {
  buy_less: { label: 'BUY LESS', color: palette.warning, background: '#FEF3C7' },
  buy_more: { label: 'BUY MORE', color: palette.fresh, background: '#DCFCE7' },
  stop_buying: { label: 'SKIP', color: palette.danger, background: '#FEE2E2' },
  well_bought: { label: 'WELL BOUGHT', color: palette.fresh, background: '#DCFCE7' },
};

const RECEIPT_CATEGORIES = [
  'produce',
  'dairy',
  'meat',
  'seafood',
  'bakery',
  'frozen',
  'deli',
  'beverage',
  'pantry_staple',
  'non_food',
  'unknown',
] as const;

const RECEIPT_UNITS = [
  'each',
  'lb',
  'oz',
  'kg',
  'g',
  'gallon',
  'liter',
  'pack',
  'bunch',
  'dozen',
] as const;

async function prepareImage(asset: ImagePicker.ImagePickerAsset): Promise<PreparedImage> {
  const manipulator = ImageManipulator.ImageManipulator.manipulate(asset.uri);
  const longEdge = Math.max(asset.width, asset.height);
  if (longEdge > 1600) {
    if (asset.width >= asset.height) {
      manipulator.resize({ width: 1600 });
    } else {
      manipulator.resize({ height: 1600 });
    }
  }
  const rendered = await manipulator.renderAsync();
  return rendered.saveAsync({
    compress: 0.8,
    format: ImageManipulator.SaveFormat.JPEG,
  });
}

function errorCopy(error: unknown): string {
  return error instanceof FreshLedgerApiError || error instanceof Error
    ? error.message
    : 'FreshLedger hit a snag — please try again.';
}

function provenanceKind(receipt: ReceiptDraft): ProvenanceKind {
  if (receipt.scan_provenance.mode === 'demo') return 'demo';
  if (
    receipt.scan_provenance.provider === 'rapidocr' &&
    !receipt.scan_provenance.ai_called
  ) {
    return 'local';
  }
  if (receipt.scan_provenance.provider === 'openai' && receipt.scan_provenance.ai_called) {
    return 'openai';
  }
  return 'unknown';
}

function optionDays(item: ReceiptItem, method: StorageMethod): number | null {
  return item.storage_options?.[`${method}_days`] ?? null;
}

function storageTemperature(method: StorageMethod, current?: number): string {
  const celsius =
    current ?? (method === 'freezer' ? -18 : method === 'pantry' ? 20 : 4);
  const fahrenheit = Math.round((celsius * 9) / 5 + 32);
  return `${fahrenheit}°F`;
}

function ReceiptItemCard({
  item,
  onNameChange,
  onItemChange,
  onStorageChange,
}: {
  item: ReceiptItem;
  onNameChange: (name: string) => void;
  onItemChange: (
    updates: Partial<
      Pick<ReceiptItem, 'qty' | 'unit' | 'unit_price' | 'category' | 'excluded'>
    >,
  ) => void;
  onStorageChange: (method: StorageMethod) => void;
}) {
  const [qtyText, setQtyText] = useState(String(item.qty));
  const [unitPriceText, setUnitPriceText] = useState(item.unit_price.toFixed(2));
  const [categoryMenuVisible, setCategoryMenuVisible] = useState(false);
  const [unitMenuVisible, setUnitMenuVisible] = useState(false);

  useEffect(() => {
    setQtyText(String(item.qty));
    setUnitPriceText(item.unit_price.toFixed(2));
  }, [item.item_id]);

  const updateNumber = (field: 'qty' | 'unit_price', value: string) => {
    if (field === 'qty') setQtyText(value);
    else setUnitPriceText(value);
    const parsed = Number(value);
    const valid =
      Number.isFinite(parsed) &&
      (field === 'qty' ? parsed > 0 && parsed <= 100 : parsed >= -500 && parsed <= 500);
    if (valid) onItemChange({ [field]: parsed });
  };

  return (
    <Card
      mode="elevated"
      style={[styles.itemCard, item.needs_review && styles.itemNeedsReview]}
    >
      <Card.Content>
        <View style={styles.itemHeader}>
          <View style={styles.itemName}>
            <Text variant="titleMedium">{item.name}</Text>
            <Text variant="bodySmall" style={styles.mutedText}>
              {Number.isInteger(item.qty) ? item.qty.toFixed(0) : item.qty} {item.unit} ·{' '}
              {item.raw_text}
            </Text>
          </View>
          <Text variant="titleMedium" style={styles.price}>
            ${item.line_total.toFixed(2)}
          </Text>
        </View>

        <View style={styles.reviewEditor}>
          <TextInput
            mode="outlined"
            dense
            label={item.needs_review ? 'Correct item name' : 'Item name'}
            value={item.name}
            onChangeText={onNameChange}
          />
          {item.needs_review && (
            <Text variant="bodySmall" style={styles.mutedText}>
              A confirmed correction can teach this store’s exact receipt alias. FreshLedger never learns from an unconfirmed guess.
            </Text>
          )}
          <View style={styles.editRow}>
            <TextInput
              mode="outlined"
              dense
              label="Quantity"
              value={qtyText}
              keyboardType="decimal-pad"
              onChangeText={(value) => updateNumber('qty', value)}
              style={styles.editField}
            />
            <TextInput
              mode="outlined"
              dense
              label="Unit price"
              value={unitPriceText}
              keyboardType="decimal-pad"
              left={<TextInput.Affix text="$" />}
              onChangeText={(value) => updateNumber('unit_price', value)}
              style={styles.editField}
            />
            <Button
              compact
              mode={item.excluded ? 'contained-tonal' : 'outlined'}
              icon={item.excluded ? 'fridge-outline' : 'book-outline'}
              onPress={() => onItemChange({ excluded: !item.excluded })}
              style={styles.excludeButton}
              disabled={item.category === 'non_food'}
            >
              {item.excluded ? 'Add to pantry' : 'Ledger only'}
            </Button>
          </View>
          <View style={styles.editRow}>
            <Menu
              visible={categoryMenuVisible}
              onDismiss={() => setCategoryMenuVisible(false)}
              anchor={
                <Button mode="outlined" onPress={() => setCategoryMenuVisible(true)}>
                  Category · {item.category.replace('_', ' ')}
                </Button>
              }
            >
              {RECEIPT_CATEGORIES.map((category) => (
                <Menu.Item
                  key={category}
                  title={category.replace('_', ' ')}
                  onPress={() => {
                    onItemChange(
                      category === 'non_food'
                        ? { category, excluded: true }
                        : { category },
                    );
                    setCategoryMenuVisible(false);
                  }}
                />
              ))}
            </Menu>
            <Menu
              visible={unitMenuVisible}
              onDismiss={() => setUnitMenuVisible(false)}
              anchor={
                <Button mode="outlined" onPress={() => setUnitMenuVisible(true)}>
                  Unit · {item.unit}
                </Button>
              }
            >
              {RECEIPT_UNITS.map((unit) => (
                <Menu.Item
                  key={unit}
                  title={unit}
                  onPress={() => {
                    onItemChange({ unit });
                    setUnitMenuVisible(false);
                  }}
                />
              ))}
            </Menu>
          </View>
          <Text variant="bodySmall" style={styles.mutedText}>
            Line total updates from quantity × unit price. “Ledger only” keeps spending history but never adds the line to pantry. Identity or category corrections are re-grounded before storage advice is saved.
          </Text>
        </View>

        {(item.storage || item.excluded) && <Divider style={styles.divider} />}
        {item.storage && !item.excluded && (
          <View>
            <Text variant="labelMedium" style={styles.fieldLabel}>
              Store it
            </Text>
            <View style={styles.chips}>
              {(['fridge', 'freezer', 'pantry'] as const).map((method) => {
                const days = optionDays(item, method);
                if (days === null) return null;
                const look = storagePresentation[method];
                const selected = item.storage?.method === method;
                return (
                  <Chip
                    key={method}
                    compact
                    icon={look.icon}
                    selected={selected}
                    onPress={() => onStorageChange(method)}
                    style={{ backgroundColor: selected ? look.background : '#F1F5F9' }}
                    textStyle={{ color: selected ? look.color : palette.muted }}
                  >
                    {method} · {days}d ·{' '}
                    {storageTemperature(
                      method,
                      selected ? item.storage?.temp_c : undefined,
                    )}
                  </Chip>
                );
              })}
            </View>
          </View>
        )}
        <View style={styles.chips}>
          {item.excluded && <Chip compact>Ledger only</Chip>}
          {item.needs_review && (
            <Chip compact icon="alert" style={styles.reviewChip}>
              Check against photo
            </Chip>
          )}
          {item.shelf_life_source === 'reference' && (
            <Chip compact icon="shield-check-outline">
              Grounded guidance
            </Chip>
          )}
        </View>
      </Card.Content>
    </Card>
  );
}

function PantryCard({
  item,
  onConsume,
  onSpoil,
  busy,
}: {
  item: PantryItem;
  onConsume: () => void;
  onSpoil: () => void;
  busy: boolean;
}) {
  const freshness = freshnessPresentation[item.freshness];
  const storage = storagePresentation[item.storage.method];
  return (
    <Card mode="contained" style={styles.pantryCard}>
      <Card.Content>
        <View style={styles.itemHeader}>
          <View style={styles.itemName}>
            <Text variant="titleMedium">{item.name}</Text>
            <Text variant="bodySmall" style={styles.mutedText}>
              {Number.isInteger(item.qty_remaining)
                ? item.qty_remaining.toFixed(0)
                : item.qty_remaining.toFixed(2)}
              /
              {Number.isInteger(item.qty_initial)
                ? item.qty_initial.toFixed(0)
                : item.qty_initial.toFixed(2)}{' '}
              {item.unit} · {item.storage.method} ·{' '}
              {storageTemperature(item.storage.method, item.storage.temp_c)}
            </Text>
          </View>
          <Chip compact icon={storage.icon} textStyle={{ color: freshness.color }}>
            {item.days_left < 0 ? `${Math.abs(item.days_left)}d past` : `${item.days_left}d left`}
          </Chip>
        </View>
        <ProgressBar
          progress={freshness.progress}
          color={freshness.color}
          style={styles.freshnessBar}
        />
      </Card.Content>
      <Card.Actions style={styles.pantryActions}>
        <Text variant="labelMedium" style={[styles.itemName, { color: freshness.color }]}>
          {freshness.label}
        </Text>
        <Button compact mode="outlined" icon="check" onPress={onConsume} disabled={busy}>
          Ate it
        </Button>
        <Button
          compact
          mode="outlined"
          icon="delete-outline"
          textColor={palette.danger}
          onPress={onSpoil}
          disabled={busy}
          loading={busy}
        >
          Tossed it
        </Button>
      </Card.Actions>
    </Card>
  );
}

function MealCard({
  meal,
  index,
  onMade,
  busy,
}: {
  meal: MealSuggestion;
  index: number;
  onMade: () => void;
  busy: boolean;
}) {
  return (
    <Card mode="contained" style={{ backgroundColor: mealBackgrounds[index % 3] }}>
      <Card.Content>
        <View style={styles.itemHeader}>
          <View style={styles.itemName}>
            <Text variant="labelMedium" style={styles.eyebrow}>
              PICK {index + 1}
            </Text>
            <Text variant="titleLarge">{meal.name}</Text>
          </View>
          <Chip compact icon="clock-outline">
            ~{meal.time_minutes} min
          </Chip>
        </View>
        <View style={styles.chips}>
          {meal.uses.map((use) => (
            <Chip key={use.pantry_item_id} compact icon="leaf">
              RESCUES {use.name} · {use.days_left}d
            </Chip>
          ))}
        </View>
        <Text variant="bodyMedium" style={styles.mealWhy}>
          {meal.why_now}
        </Text>
        <Text variant="bodySmall" style={styles.mutedText}>
          {meal.steps.join(' ')}
        </Text>
        <Text variant="bodySmall" style={styles.safetyNote}>
          {meal.safety_note}
        </Text>
        <Button
          mode="contained-tonal"
          icon="silverware-fork-knife"
          onPress={onMade}
          disabled={busy}
          loading={busy}
          style={styles.mealButton}
        >
          I made this
        </Button>
      </Card.Content>
    </Card>
  );
}

export default function App() {
  const [mode, setMode] = useState<Mode>('demo');
  const [scanState, setScanState] = useState<ScanState>('idle');
  const [previewSource, setPreviewSource] = useState<ImageSourcePropType | null>(null);
  const [receipt, setReceipt] = useState<ReceiptDraft | null>(null);
  const [pantry, setPantry] = useState<PantryResponse | null>(null);
  const [meals, setMeals] = useState<MealsResponse | null>(null);
  const [insights, setInsights] = useState<InsightsResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [activeSample, setActiveSample] = useState('r2');
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [busyPantryItemId, setBusyPantryItemId] = useState<number | null>(null);
  const [selectedMeal, setSelectedMeal] = useState<MealSuggestion | null>(null);
  const [busyMeal, setBusyMeal] = useState(false);
  const [dashboardRefreshing, setDashboardRefreshing] = useState(false);

  const selectedSample = useMemo(
    () => SAMPLES.find((sample) => sample.id === activeSample) ?? SAMPLES[0],
    [activeSample],
  );
  const isWorking = ['preparing', 'reading', 'confirming'].includes(scanState);
  const maxCategorySpend = useMemo(
    () => Math.max(1, ...(insights?.by_category.map((row) => row.spent) ?? [])),
    [insights],
  );
  const rescueItemCount = useMemo(
    () =>
      new Set(
        meals?.meals.flatMap((meal) =>
          meal.uses
            .filter((use) => use.days_left <= 3)
            .map((use) => use.pantry_item_id),
        ) ?? [],
      ).size,
    [meals],
  );
  const reviewItemCount = receipt?.items.filter((item) => item.needs_review).length ?? 0;
  const receiptProvenance = receipt ? provenanceKind(receipt) : null;

  const resetResult = () => {
    setReceipt(null);
    setPantry(null);
    setMeals(null);
    setInsights(null);
    setSelectedMeal(null);
    setErrorMessage(null);
  };

  const startDemo = async (sample: SampleDefinition = selectedSample) => {
    setMode('demo');
    setActiveSample(sample.id);
    resetResult();
    setPreviewSource(sample.image);
    setScanState('reading');
    try {
      const draft = await scanDemoReceipt(sample.id);
      setReceipt(draft);
      setScanState('done');
    } catch (error) {
      setErrorMessage(errorCopy(error));
      setScanState('error');
    }
  };

  const processAsset = async (asset: ImagePicker.ImagePickerAsset) => {
    setMode('local');
    resetResult();
    setPreviewSource({ uri: asset.uri });
    setScanState('preparing');
    try {
      // Expo 57 allows Web picker assets to report zero dimensions while
      // exposing the original File for direct FormData upload. The server
      // validates and resizes every upload, so Web skips the canvas path.
      const prepared =
        Platform.OS === 'web'
          ? { uri: asset.uri, width: asset.width, height: asset.height }
          : await prepareImage(asset);
      setPreviewSource({ uri: prepared.uri });
      setScanState('reading');
      const draft = await scanReceipt({
        uri: prepared.uri,
        mimeType: Platform.OS === 'web' ? (asset.mimeType ?? 'image/jpeg') : 'image/jpeg',
        fileName: Platform.OS === 'web' ? (asset.fileName ?? 'receipt.jpg') : 'receipt.jpg',
        webFile: Platform.OS === 'web' ? (asset.file ?? undefined) : undefined,
      });
      setReceipt(draft);
      setScanState('done');
    } catch (error) {
      setErrorMessage(errorCopy(error));
      setScanState('error');
    }
  };

  const pickFromLibrary = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      allowsEditing: false,
      quality: 1,
    });
    if (!result.canceled) await processAsset(result.assets[0]);
  };

  const takePhoto = async () => {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) {
      setErrorMessage('Camera permission is required to photograph a receipt.');
      setScanState('error');
      return;
    }
    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ['images'],
      allowsEditing: false,
      quality: 1,
      cameraType: ImagePicker.CameraType.back,
    });
    if (!result.canceled) await processAsset(result.assets[0]);
  };

  const updateStorage = (itemId: number, method: StorageMethod) => {
    setReceipt((current) => {
      if (!current) return current;
      return {
        ...current,
        items: current.items.map((item) => {
          const duration = optionDays(item, method);
          if (item.item_id !== itemId || !item.storage || duration === null) return item;
          return {
            ...item,
            storage: {
              method,
              duration_days: duration,
              temp_c: method === 'freezer' ? -18 : method === 'pantry' ? 20 : 4,
            },
            eat_by_window: item.eat_by_window
              ? {
                  start_days: 0,
                  end_days: duration,
                }
              : { start_days: 0, end_days: duration },
          };
        }),
      };
    });
  };

  const updateItemName = (itemId: number, name: string) => {
    setReceipt((current) => {
      if (!current) return current;
      return {
        ...current,
        items: current.items.map((item) =>
          item.item_id === itemId ? applyIdentitySafety(item, { name }) : item,
        ),
      };
    });
  };

  const updateItemDetails = (
    itemId: number,
    updates: Partial<
      Pick<ReceiptItem, 'qty' | 'unit' | 'unit_price' | 'category' | 'excluded'>
    >,
  ) => {
    setReceipt((current) => {
      if (!current) return current;
      return {
        ...current,
        items: current.items.map((item) => {
          if (item.item_id !== itemId) return item;
          const qty = updates.qty ?? item.qty;
          const unitPrice = updates.unit_price ?? item.unit_price;
          const updated = {
            ...item,
            line_total: Math.round(qty * unitPrice * 100) / 100,
          };
          return applyIdentitySafety(updated, updates);
        }),
      };
    });
  };

  const refreshDashboard = async () => {
    const [pantryResult, mealsResult, insightsResult] = await Promise.allSettled([
      getPantry(),
      getMeals(),
      getInsights(),
    ]);
    if (pantryResult.status === 'fulfilled') setPantry(pantryResult.value);
    if (mealsResult.status === 'fulfilled') setMeals(mealsResult.value);
    if (insightsResult.status === 'fulfilled') setInsights(insightsResult.value);
    const failure = [pantryResult, mealsResult, insightsResult].find(
      (result) => result.status === 'rejected',
    );
    if (failure?.status === 'rejected') throw failure.reason;
  };

  const retryDashboard = async () => {
    setErrorMessage(null);
    setDashboardRefreshing(true);
    try {
      await refreshDashboard();
    } catch (error) {
      setErrorMessage(`Your receipt is saved, but the dashboard could not refresh. ${errorCopy(error)}`);
    } finally {
      setDashboardRefreshing(false);
    }
  };

  const openJudgeDemo = async () => {
    resetResult();
    setPreviewSource(null);
    setScanState('reading');
    let seeded = false;
    try {
      const result = await seedDemo();
      seeded = true;
      setScanState('confirmed');
      await refreshDashboard();
      setFeedbackMessage(
        `Judge demo loaded — ${result.receipts} receipts and ${result.pantry_items} pantry items, with 0 AI calls.`,
      );
    } catch (error) {
      if (seeded) {
        setScanState('confirmed');
        setErrorMessage(
          `Demo data was loaded, but the dashboard could not refresh. ${errorCopy(error)}`,
        );
      } else {
        setErrorMessage(errorCopy(error));
        setScanState('error');
      }
    }
  };

  const confirmCurrentReceipt = async () => {
    if (!receipt) return;
    setErrorMessage(null);
    setScanState('confirming');
    let committed = false;
    try {
      await confirmReceipt(receipt);
      committed = true;
      setScanState('confirmed');
      await refreshDashboard();
    } catch (error) {
      if (committed) {
        setErrorMessage(
          `Receipt saved to your fridge, but the dashboard could not refresh. ${errorCopy(error)}`,
        );
      } else {
        setErrorMessage(errorCopy(error));
        setScanState('error');
      }
    }
  };

  const mutatePantry = async (itemId: number, action: 'consume' | 'spoil') => {
    setErrorMessage(null);
    setBusyPantryItemId(itemId);
    try {
      if (action === 'consume') {
        await consumePantryItem(itemId);
        setFeedbackMessage('Marked as eaten — nice save.');
      } else {
        const result = await spoilPantryItem(itemId);
        setFeedbackMessage(
          `$${result.waste_event.cost_lost.toFixed(2)} added to waste — we'll factor this into your buying advice.`,
        );
      }
      setPantry((current) =>
        current
          ? { ...current, items: current.items.filter((item) => item.pantry_item_id !== itemId) }
          : current,
      );
      try {
        await refreshDashboard();
      } catch (error) {
        setErrorMessage(`Action saved, but the dashboard could not refresh. ${errorCopy(error)}`);
      }
    } catch (error) {
      setErrorMessage(errorCopy(error));
    } finally {
      setBusyPantryItemId(null);
    }
  };

  const completeMeal = async () => {
    if (!selectedMeal) return;
    setErrorMessage(null);
    setBusyMeal(true);
    try {
      const uniqueIds = [...new Set(selectedMeal.uses.map((use) => use.pantry_item_id))];
      await consumeMealItems(uniqueIds);
      setFeedbackMessage(
        `Meal logged — ${uniqueIds.length} fridge ${uniqueIds.length === 1 ? 'item' : 'items'} marked as used.`,
      );
      setSelectedMeal(null);
      setPantry((current) =>
        current
          ? {
              ...current,
              items: current.items.filter(
                (item) => !uniqueIds.includes(item.pantry_item_id),
              ),
            }
          : current,
      );
      try {
        await refreshDashboard();
      } catch (error) {
        setErrorMessage(`Meal saved, but the dashboard could not refresh. ${errorCopy(error)}`);
      }
    } catch (error) {
      setErrorMessage(errorCopy(error));
    } finally {
      setBusyMeal(false);
    }
  };

  return (
    <SafeAreaProvider>
      <PaperProvider theme={theme}>
        <View style={styles.container}>
          <StatusBar style="dark" />
          <Appbar.Header elevated>
            <Appbar.Content title="FreshLedger" />
            <Chip compact icon="leaf" style={styles.headerChip}>
              Build Week demo
            </Chip>
          </Appbar.Header>
          <ScrollView contentContainerStyle={styles.content}>
            <View style={styles.hero}>
              <Text variant="labelLarge" style={styles.eyebrow}>
                BUY IT · STORE IT · USE IT
              </Text>
              <Text variant="displaySmall" style={styles.heroTitle}>
                Your groceries, before they become waste.
              </Text>
              <Text variant="bodyLarge" style={styles.heroCopy}>
                Turn a receipt into a freshness-aware inventory, then act on what expires first.
              </Text>
            </View>

            <SegmentedButtons
              value={mode}
              onValueChange={(value) => {
                setMode(value as Mode);
                resetResult();
                setPreviewSource(null);
                setScanState('idle');
              }}
              buttons={[
                { value: 'demo', label: 'Demo', icon: 'play-circle-outline' },
                { value: 'local', label: 'Local OCR', icon: 'text-box-search-outline' },
                { value: 'gpt', label: 'GPT · future', icon: 'robot-outline' },
              ]}
            />

            {mode === 'demo' ? (
              <Card mode="contained" style={styles.modeCard}>
                <Card.Content>
                  <View style={styles.modeHeading}>
                    <View style={styles.itemName}>
                      <Text variant="headlineSmall">Try a saved receipt</Text>
                      <Text variant="bodyMedium" style={styles.mutedText}>
                        Deterministic sample data runs through the real safety, review, and pantry pipeline.
                      </Text>
                    </View>
                    <Chip compact icon="cash-remove" style={styles.zeroTokenChip}>
                      0 tokens
                    </Chip>
                  </View>
                  <View style={styles.sampleGrid}>
                    {SAMPLES.map((sample) => (
                      <Surface
                        key={sample.id}
                        elevation={sample.id === activeSample ? 2 : 0}
                        style={[
                          styles.sampleTile,
                          sample.id === activeSample && styles.sampleTileSelected,
                        ]}
                      >
                        <Image source={sample.image} style={styles.sampleThumb} />
                        <Text variant="titleSmall">{sample.title}</Text>
                        <Text variant="bodySmall" style={styles.mutedText}>
                          {sample.subtitle}
                        </Text>
                        <Button
                          compact
                          mode={sample.id === activeSample ? 'contained-tonal' : 'text'}
                          onPress={() => setActiveSample(sample.id)}
                        >
                          {sample.id === activeSample ? 'Selected' : 'Choose'}
                        </Button>
                      </Surface>
                    ))}
                  </View>
                  <Button
                    mode="contained"
                    icon="receipt-text-check-outline"
                    onPress={() => startDemo()}
                    disabled={isWorking}
                    contentStyle={styles.primaryButton}
                  >
                    Run sample through FreshLedger
                  </Button>
                  <Button
                    mode="outlined"
                    icon="chart-box-outline"
                    onPress={openJudgeDemo}
                    disabled={isWorking}
                    style={styles.secondaryDemoButton}
                  >
                    Open full judge demo
                  </Button>
                  <Text variant="bodySmall" style={styles.disclosure}>
                    Synthetic samples · saved parses · no OpenAI API call. The full demo reloads all three fixtures before opening the dashboard.
                  </Text>
                </Card.Content>
              </Card>
            ) : mode === 'local' ? (
              <Card mode="contained" style={styles.modeCard}>
                <Card.Content>
                  <View style={styles.modeHeading}>
                    <View style={styles.itemName}>
                      <Text variant="headlineSmall">Local Scan Beta</Text>
                      <Text variant="bodyMedium" style={styles.mutedText}>
                        RapidOCR reads the photo on your FreshLedger server, then deterministic rules reconstruct and reconcile the receipt.
                      </Text>
                    </View>
                    <Chip compact icon="cloud-off-outline" style={styles.zeroTokenChip}>
                      0 cloud calls
                    </Chip>
                  </View>
                  <Text variant="bodyMedium" style={styles.mutedText}>
                    Built-in product aliases improve matching. Confirmed name corrections improve exact merchant matching over time; uncertain lines are never silently guessed.
                  </Text>
                  <View style={styles.actions}>
                    {Platform.OS !== 'web' && (
                      <Button mode="contained" icon="camera" onPress={takePhoto} disabled={isWorking}>
                        Take photo
                      </Button>
                    )}
                    <Button mode="outlined" icon="image" onPress={pickFromLibrary} disabled={isWorking}>
                      Choose photo
                    </Button>
                  </View>
                  <Text variant="bodySmall" style={styles.disclosure}>
                    Local OCR · no API key · no OpenAI call. The submitted server defaults to this engine; the result below always discloses what actually ran.
                  </Text>
                </Card.Content>
              </Card>
            ) : (
              <Card mode="outlined" style={[styles.modeCard, styles.futureCard]}>
                <Card.Content>
                  <View style={styles.modeHeading}>
                    <View style={styles.itemName}>
                      <Text variant="headlineSmall">Optional GPT-5.6 vision</Text>
                      <Text variant="bodyMedium" style={styles.mutedText}>
                        Future deployments can opt into the retained OpenAI adapter with their own API key.
                      </Text>
                    </View>
                    <Chip compact icon="clock-outline">Future option</Chip>
                  </View>
                  <Button mode="outlined" icon="lock-outline" disabled style={styles.futureButton}>
                    Not enabled in this build
                  </Button>
                  <Text variant="bodySmall" style={styles.disclosure}>
                    This screen cannot trigger an API call. Choose Local OCR for a real photo or Demo for a repeatable saved receipt.
                  </Text>
                </Card.Content>
              </Card>
            )}

            {previewSource && (
              <Image source={previewSource} style={styles.preview} resizeMode="contain" />
            )}

            {(scanState === 'preparing' || scanState === 'reading' || scanState === 'confirming') && (
              <Card>
                <Card.Content style={styles.loadingRow}>
                  <ActivityIndicator />
                  <View style={styles.loadingCopy}>
                    <Text variant="titleMedium">
                      {scanState === 'preparing'
                        ? 'Preparing your photo…'
                        : scanState === 'confirming'
                          ? 'Stocking your fridge…'
                          : mode === 'demo'
                            ? 'Loading saved sample…'
                            : 'Local OCR is reading your receipt…'}
                    </Text>
                    <Text variant="bodySmall" style={styles.mutedText}>
                      {mode === 'demo'
                        ? 'No AI call: the saved parse is going through the same safety and persistence pipeline.'
                        : 'OCR, receipt rules, and product memory are reconstructing visible lines with zero cloud-AI calls.'}
                    </Text>
                  </View>
                </Card.Content>
              </Card>
            )}

            {scanState === 'error' && errorMessage && (
              <Card mode="outlined" style={styles.errorCard}>
                <Card.Content>
                  <Text variant="titleMedium">FreshLedger needs another try</Text>
                  <Text variant="bodyMedium">{errorMessage}</Text>
                  {mode === 'local' && (
                    <Button mode="text" icon="play-circle-outline" onPress={() => startDemo(SAMPLES[0])}>
                      Try Demo Mode instead
                    </Button>
                  )}
                </Card.Content>
              </Card>
            )}

            {scanState !== 'error' && errorMessage && (
              <Card mode="outlined" style={styles.errorCard}>
                <Card.Content>
                  <Text variant="bodyMedium">{errorMessage}</Text>
                </Card.Content>
              </Card>
            )}

            {receipt && (
              <View style={styles.results}>
                <Card
                  mode="contained"
                  style={
                    receiptProvenance === 'demo'
                      ? styles.demoDisclosureCard
                      : receiptProvenance === 'local'
                        ? styles.localDisclosureCard
                        : styles.liveDisclosureCard
                  }
                >
                  <Card.Content style={styles.provenanceRow}>
                    <Chip
                      compact
                      icon={
                        receiptProvenance === 'demo'
                          ? 'database-outline'
                          : receiptProvenance === 'local'
                            ? 'text-box-search-outline'
                            : receiptProvenance === 'openai'
                              ? 'robot-outline'
                              : 'help-circle-outline'
                      }
                    >
                      {receiptProvenance === 'demo'
                        ? 'Sample data · no AI call'
                        : receiptProvenance === 'local'
                          ? 'Local OCR · 0 cloud calls'
                          : receiptProvenance === 'openai'
                            ? `Optional live · ${receipt.scan_provenance.model ?? 'GPT-5.6'}`
                            : 'Scan source unavailable'}
                    </Chip>
                    <Text variant="bodySmall" style={styles.provenanceCopy}>
                      {receiptProvenance === 'demo'
                        ? `Fixture ${receipt.scan_provenance.fixture_id?.toUpperCase()}`
                        : receiptProvenance === 'local'
                          ? `${receipt.scan_provenance.model ?? 'RapidOCR'} · on-server OCR · no API key`
                          : receiptProvenance === 'openai'
                            ? 'OpenAI Structured Outputs · API call disclosed'
                            : 'Review this receipt before confirming'}
                    </Text>
                  </Card.Content>
                </Card>

                <View>
                  <Text variant="headlineSmall">Review your receipt</Text>
                  <Text variant="bodyMedium" style={styles.mutedText}>
                    {receipt.store_name ?? 'Receipt'} · {receipt.purchased_at ?? 'Date not visible'} ·{' '}
                    {receipt.items.length} items
                  </Text>
                </View>

                {reviewItemCount > 0 && (
                  <Card mode="outlined" style={styles.warningCard}>
                    <Card.Content>
                      <Text variant="titleMedium">
                        {reviewItemCount} uncertain {reviewItemCount === 1 ? 'line needs' : 'lines need'} a quick check
                      </Text>
                      <Text variant="bodyMedium" style={styles.mutedText}>
                        {receiptProvenance === 'local'
                          ? 'Local OCR did not confidently resolve every field. Compare each highlighted line with the photo; correct its details and choose which food lines enter pantry.'
                          : 'Compare each highlighted line with the receipt photo before you confirm it.'}
                      </Text>
                    </Card.Content>
                  </Card>
                )}

                {receipt.reconciliation.status === 'mismatch' && (
                  <Card mode="outlined" style={styles.warningCard}>
                    <Card.Content>
                      <Text>Check the items — the printed subtotal and parsed lines differ.</Text>
                    </Card.Content>
                  </Card>
                )}

                {receipt.items.map((item) => (
                  <ReceiptItemCard
                    key={item.item_id}
                    item={item}
                    onNameChange={(name) => updateItemName(item.item_id, name)}
                    onItemChange={(updates) => updateItemDetails(item.item_id, updates)}
                    onStorageChange={(method) => updateStorage(item.item_id, method)}
                  />
                ))}

                {scanState !== 'confirmed' && (
                  <Button
                    mode="contained"
                    icon="fridge-outline"
                    onPress={confirmCurrentReceipt}
                    disabled={isWorking}
                    contentStyle={styles.primaryButton}
                  >
                    {reviewItemCount > 0
                      ? 'Confirm after review & add to my fridge'
                      : 'Confirm & add to my fridge'}
                  </Button>
                )}
              </View>
            )}

            {scanState === 'confirmed' && pantry && (
              <View style={styles.results}>
                <Card mode="contained" style={styles.successCard}>
                  <Card.Content>
                    <Text variant="titleLarge">Your fridge is ready</Text>
                    <Text variant="bodyMedium">
                      {pantry.items.length} active items · ${pantry.value_in_stock.toFixed(2)} in stock
                    </Text>
                  </Card.Content>
                </Card>
                <View>
                  <Text variant="headlineSmall">Use these first</Text>
                  <Text variant="bodyMedium" style={styles.mutedText}>
                    Best-by dates are grounded on the server and recalculated each day.
                  </Text>
                </View>
                {pantry.items.map((item) => (
                  <PantryCard
                    key={item.pantry_item_id}
                    item={item}
                    onConsume={() => mutatePantry(item.pantry_item_id, 'consume')}
                    onSpoil={() => mutatePantry(item.pantry_item_id, 'spoil')}
                    busy={busyPantryItemId === item.pantry_item_id}
                  />
                ))}

                {meals && (
                  <View style={styles.dashboardSection}>
                    <View style={styles.sectionHeading}>
                      <View style={styles.itemName}>
                        <Text variant="headlineSmall">Today&apos;s rescue meals</Text>
                        <Text variant="bodyMedium" style={styles.mutedText}>
                          Prioritizing {rescueItemCount} pantry{' '}
                          {rescueItemCount === 1 ? 'item' : 'items'} expiring soon.
                        </Text>
                      </View>
                      <Chip compact icon="database-outline" style={styles.zeroTokenChip}>
                        Demo suggestions · 0 tokens
                      </Chip>
                    </View>
                    {meals.meals.length > 0 ? (
                      meals.meals.map((meal, index) => (
                        <MealCard
                          key={`${meal.name}-${index}`}
                          meal={meal}
                          index={index}
                          onMade={() => setSelectedMeal(meal)}
                          busy={busyMeal}
                        />
                      ))
                    ) : (
                      <Card mode="outlined">
                        <Card.Content>
                          <Text>Nothing active is available for a rescue meal yet.</Text>
                        </Card.Content>
                      </Card>
                    )}
                  </View>
                )}

                {insights && (
                  <View style={styles.dashboardSection}>
                    <View style={styles.sectionHeading}>
                      <View style={styles.itemName}>
                        <Text variant="headlineSmall">Your grocery story</Text>
                        <Text variant="bodyMedium" style={styles.mutedText}>
                          Exact SQLite totals from {insights.totals.receipt_count}{' '}
                          {insights.totals.receipt_count === 1 ? 'receipt' : 'receipts'}.
                        </Text>
                      </View>
                      <Chip compact icon="calculator-variant-outline">
                        Computed · no AI call
                      </Chip>
                    </View>

                    <View style={styles.metricGrid}>
                      <Surface elevation={1} style={styles.metricTile}>
                        <Text variant="labelLarge" style={styles.mutedText}>
                          SPENT
                        </Text>
                        <Text variant="headlineMedium" style={styles.metricValue}>
                          ${insights.totals.spent.toFixed(2)}
                        </Text>
                        <Text variant="bodySmall" style={styles.mutedText}>
                          ${insights.totals.food_spent.toFixed(2)} on food
                        </Text>
                      </Surface>
                      <Surface elevation={1} style={[styles.metricTile, styles.wasteMetric]}>
                        <Text variant="labelLarge" style={{ color: palette.danger }}>
                          WASTED
                        </Text>
                        <Text
                          variant="headlineMedium"
                          style={[styles.metricValue, { color: palette.danger }]}
                        >
                          ${insights.totals.wasted.toFixed(2)}
                        </Text>
                        <Text variant="bodySmall" style={styles.mutedText}>
                          {(insights.totals.waste_rate * 100).toFixed(1)}% of spend
                        </Text>
                      </Surface>
                    </View>

                    <Card mode="contained" style={styles.itemCard}>
                      <Card.Content>
                        <Text variant="titleMedium">Spend by category</Text>
                        {insights.by_category.map((row) => (
                          <View key={row.category} style={styles.categoryRow}>
                            <View style={styles.categoryLabels}>
                              <Text variant="bodyMedium">{row.category.replace('_', ' ')}</Text>
                              <Text variant="labelMedium">${row.spent.toFixed(2)}</Text>
                            </View>
                            <ProgressBar
                              progress={row.spent / maxCategorySpend}
                              color={palette.blue}
                              style={styles.categoryBar}
                            />
                          </View>
                        ))}
                      </Card.Content>
                    </Card>

                    <Card mode="outlined" style={styles.wasteListCard}>
                      <Card.Content>
                        <Text variant="titleMedium">
                          Where the ${insights.totals.wasted.toFixed(2)} went
                        </Text>
                        {insights.waste_events.length === 0 ? (
                          <Text variant="bodyMedium" style={styles.mutedText}>
                            No waste logged yet — that&apos;s the goal.
                          </Text>
                        ) : (
                          insights.waste_events.slice(0, 6).map((event, index) => (
                            <View
                              key={`${event.name}-${event.occurred_at}-${index}`}
                              style={styles.wasteRow}
                            >
                              <Text variant="bodyMedium" style={styles.itemName}>
                                {event.name} · tossed {event.occurred_at.slice(0, 10)}
                              </Text>
                              <Text variant="labelLarge" style={{ color: palette.danger }}>
                                ${event.cost_lost.toFixed(2)}
                              </Text>
                            </View>
                          ))
                        )}
                      </Card.Content>
                    </Card>

                    <View>
                      <Text variant="titleLarge">Buy smarter next trip</Text>
                      <Text variant="bodySmall" style={styles.mutedText}>
                        Deterministic demo advice derived only from the totals above.
                      </Text>
                    </View>
                    {insights.advice.map((advice, index) => {
                      const look = advicePresentation[advice.kind];
                      return (
                        <Card
                          key={`${advice.kind}-${advice.canonical_key ?? index}`}
                          mode="contained"
                          style={{ backgroundColor: look.background }}
                        >
                          <Card.Content>
                            <Chip
                              compact
                              style={styles.adviceChip}
                              textStyle={{ color: look.color }}
                            >
                              {look.label}
                            </Chip>
                            <Text variant="bodyLarge" style={styles.adviceText}>
                              {advice.text}
                            </Text>
                          </Card.Content>
                        </Card>
                      );
                    })}
                  </View>
                )}
              </View>
            )}

            {scanState === 'confirmed' && !pantry && (
              <Card mode="contained" style={styles.successCard}>
                <Card.Content>
                  <Text variant="titleLarge">Receipt saved</Text>
                  <Text variant="bodyMedium" style={styles.mutedText}>
                    Your items are safely in the ledger. Refresh the dashboard to see the latest fridge.
                  </Text>
                  <Button
                    mode="contained-tonal"
                    icon="refresh"
                    onPress={retryDashboard}
                    loading={dashboardRefreshing}
                    disabled={dashboardRefreshing}
                    style={styles.mealButton}
                  >
                    Refresh dashboard
                  </Button>
                </Card.Content>
              </Card>
            )}
          </ScrollView>
          <Portal>
            <Dialog visible={selectedMeal !== null} onDismiss={() => setSelectedMeal(null)}>
              <Dialog.Title>Log this meal?</Dialog.Title>
              <Dialog.Content>
                <Text variant="bodyMedium">
                  FreshLedger will mark each listed pantry entry as fully used:
                </Text>
                <View style={styles.chips}>
                  {selectedMeal?.uses.map((use) => (
                    <Chip key={use.pantry_item_id} icon="check-circle-outline">
                      {use.name}
                    </Chip>
                  ))}
                </View>
              </Dialog.Content>
              <Dialog.Actions>
                <Button onPress={() => setSelectedMeal(null)} disabled={busyMeal}>
                  Cancel
                </Button>
                <Button onPress={completeMeal} loading={busyMeal} disabled={busyMeal}>
                  Confirm used
                </Button>
              </Dialog.Actions>
            </Dialog>
          </Portal>
          <Snackbar
            visible={feedbackMessage !== null}
            onDismiss={() => setFeedbackMessage(null)}
            duration={5000}
            action={{ label: 'Got it', onPress: () => setFeedbackMessage(null) }}
          >
            {feedbackMessage ?? ''}
          </Snackbar>
        </View>
      </PaperProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: palette.background },
  content: {
    width: '100%',
    maxWidth: 860,
    alignSelf: 'center',
    padding: 16,
    paddingBottom: 64,
    gap: 16,
  },
  headerChip: { marginRight: 12, backgroundColor: '#CCFBF1' },
  hero: { paddingVertical: 12, gap: 8 },
  eyebrow: { color: palette.primary, letterSpacing: 1.4 },
  heroTitle: { color: palette.ink, fontWeight: '800', lineHeight: 46 },
  heroCopy: { color: palette.muted, maxWidth: 620 },
  modeCard: { backgroundColor: palette.surface },
  modeHeading: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  zeroTokenChip: { backgroundColor: '#DCFCE7' },
  mutedText: { color: palette.muted, marginTop: 4 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 20 },
  sampleGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginVertical: 18 },
  sampleTile: {
    flexGrow: 1,
    flexBasis: 190,
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#E2E8F0',
    backgroundColor: '#FFFFFF',
  },
  sampleTileSelected: { borderColor: palette.primary, backgroundColor: '#F0FDFA' },
  sampleThumb: { width: '100%', height: 128, borderRadius: 8, marginBottom: 10 },
  primaryButton: { minHeight: 48 },
  secondaryDemoButton: { marginTop: 10 },
  futureCard: { borderColor: '#CBD5E1' },
  futureButton: { marginTop: 18 },
  disclosure: { color: palette.muted, textAlign: 'center', marginTop: 10 },
  preview: { width: '100%', height: 300, borderRadius: 12, backgroundColor: '#E2E8F0' },
  loadingRow: { flexDirection: 'row', alignItems: 'center' },
  loadingCopy: { flex: 1, marginLeft: 16 },
  errorCard: { borderColor: palette.danger },
  warningCard: { borderColor: palette.warning },
  results: { gap: 12 },
  demoDisclosureCard: { backgroundColor: '#FEF3C7' },
  localDisclosureCard: { backgroundColor: '#DCFCE7' },
  liveDisclosureCard: { backgroundColor: '#CFFAFE' },
  provenanceRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 10 },
  provenanceCopy: { color: palette.muted },
  itemCard: { backgroundColor: palette.surface },
  itemNeedsReview: { borderWidth: 2, borderColor: palette.warning },
  reviewEditor: { marginTop: 12 },
  editRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8 },
  editField: { flexGrow: 1, flexBasis: 150 },
  excludeButton: { alignSelf: 'center' },
  itemHeader: { flexDirection: 'row', alignItems: 'flex-start' },
  itemName: { flex: 1, paddingRight: 12 },
  price: { fontVariant: ['tabular-nums'], fontWeight: '700' },
  divider: { marginVertical: 12 },
  fieldLabel: { color: palette.muted, marginBottom: 7 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8 },
  reviewChip: { backgroundColor: '#FEF3C7' },
  successCard: { backgroundColor: '#DCFCE7' },
  pantryCard: { backgroundColor: '#FFFFFF' },
  freshnessBar: { height: 7, borderRadius: 4, marginTop: 14 },
  pantryActions: { alignItems: 'center', paddingHorizontal: 16, paddingBottom: 12 },
  dashboardSection: { gap: 12, marginTop: 14 },
  sectionHeading: { flexDirection: 'row', alignItems: 'flex-start', flexWrap: 'wrap', gap: 10 },
  mealWhy: { marginTop: 14, color: palette.ink, fontWeight: '600' },
  safetyNote: { color: palette.muted, fontStyle: 'italic', marginTop: 9 },
  mealButton: { alignSelf: 'flex-end', marginTop: 14 },
  metricGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  metricTile: {
    flexGrow: 1,
    flexBasis: 240,
    padding: 18,
    borderRadius: 14,
    backgroundColor: palette.surface,
  },
  wasteMetric: { borderLeftWidth: 4, borderLeftColor: palette.danger },
  metricValue: { marginTop: 4, fontWeight: '800', fontVariant: ['tabular-nums'] },
  categoryRow: { marginTop: 14 },
  categoryLabels: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 5 },
  categoryBar: { height: 8, borderRadius: 4 },
  wasteListCard: { borderColor: '#FCA5A5', borderLeftWidth: 4 },
  wasteRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 12,
    paddingVertical: 9,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.border,
  },
  adviceChip: { alignSelf: 'flex-start', backgroundColor: '#FFFFFFB8' },
  adviceText: { marginTop: 10 },
});
