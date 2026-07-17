import { useState } from 'react';
import { Image, Platform, ScrollView, StyleSheet, View } from 'react-native';
import * as ImageManipulator from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';
import { StatusBar } from 'expo-status-bar';
import {
  ActivityIndicator,
  Appbar,
  Button,
  Card,
  Chip,
  Divider,
  PaperProvider,
  Text,
} from 'react-native-paper';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { ScanApiError, scanReceipt } from './src/api';
import { theme } from './src/theme';
import type { ReceiptDraft } from './src/types';

type ScanState = 'idle' | 'preparing' | 'reading' | 'done' | 'error';

interface PreparedImage {
  uri: string;
  width: number;
  height: number;
}

async function prepareImage(asset: ImagePicker.ImagePickerAsset): Promise<PreparedImage> {
  const manipulator = ImageManipulator.ImageManipulator.manipulate(asset.uri);
  const longEdge = Math.max(asset.width, asset.height);
  if (longEdge > 1600) {
    if (asset.width >= asset.height) {
      manipulator.resize({ width: 1600, height: null });
    } else {
      manipulator.resize({ width: null, height: 1600 });
    }
  }
  const rendered = await manipulator.renderAsync();
  return rendered.saveAsync({
    compress: 0.8,
    format: ImageManipulator.SaveFormat.JPEG,
  });
}

export default function App() {
  const [scanState, setScanState] = useState<ScanState>('idle');
  const [previewUri, setPreviewUri] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ReceiptDraft | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const processAsset = async (asset: ImagePicker.ImagePickerAsset) => {
    setReceipt(null);
    setErrorMessage(null);
    setPreviewUri(asset.uri);
    setScanState('preparing');
    try {
      const prepared = await prepareImage(asset);
      setPreviewUri(prepared.uri);
      setScanState('reading');
      const draft = await scanReceipt({
        uri: prepared.uri,
        mimeType: 'image/jpeg',
        fileName: 'receipt.jpg',
      });
      setReceipt(draft);
      setScanState('done');
    } catch (error) {
      setErrorMessage(
        error instanceof ScanApiError || error instanceof Error
          ? error.message
          : 'The receipt reader hiccuped — please try scanning again.',
      );
      setScanState('error');
    }
  };

  const pickFromLibrary = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      allowsEditing: false,
      quality: 1,
    });
    if (!result.canceled) {
      await processAsset(result.assets[0]);
    }
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
    if (!result.canceled) {
      await processAsset(result.assets[0]);
    }
  };

  const isWorking = scanState === 'preparing' || scanState === 'reading';

  return (
    <SafeAreaProvider>
      <PaperProvider theme={theme}>
        <View style={styles.container}>
          <StatusBar style="dark" />
          <Appbar.Header elevated>
            <Appbar.Content title="FreshLedger" subtitle="Receipt scan walking skeleton" />
          </Appbar.Header>
          <ScrollView contentContainerStyle={styles.content}>
            <Card mode="contained">
              <Card.Content>
                <Text variant="headlineSmall">Snap a grocery receipt</Text>
                <Text variant="bodyMedium" style={styles.mutedText}>
                  One photo goes to FastAPI, through GPT-5.6 vision, and comes back as an
                  itemized ledger.
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
              </Card.Content>
            </Card>

            {previewUri && (
              <Image source={{ uri: previewUri }} style={styles.preview} resizeMode="contain" />
            )}

            {isWorking && (
              <Card>
                <Card.Content style={styles.loadingRow}>
                  <ActivityIndicator />
                  <View style={styles.loadingCopy}>
                    <Text variant="titleMedium">
                      {scanState === 'preparing' ? 'Preparing your photo…' : 'Reading your receipt…'}
                    </Text>
                    <Text variant="bodySmall" style={styles.mutedText}>
                      {scanState === 'preparing'
                        ? 'Downscaling and compressing for a clear, efficient upload.'
                        : 'GPT-5.6 is itemizing every visible line.'}
                    </Text>
                  </View>
                </Card.Content>
              </Card>
            )}

            {scanState === 'error' && errorMessage && (
              <Card mode="outlined" style={styles.errorCard}>
                <Card.Content>
                  <Text variant="titleMedium">Scan failed</Text>
                  <Text variant="bodyMedium">{errorMessage}</Text>
                </Card.Content>
              </Card>
            )}

            {receipt && (
              <View style={styles.results}>
                <View>
                  <Text variant="headlineSmall">{receipt.store_name ?? 'Receipt'}</Text>
                  <Text variant="bodyMedium" style={styles.mutedText}>
                    {receipt.purchased_at ?? 'Date not visible'} · {receipt.items.length} parsed items
                  </Text>
                </View>
                {receipt.reconciliation.status === 'mismatch' && (
                  <Card mode="outlined" style={styles.warningCard}>
                    <Card.Content>
                      <Text>Check the items — the printed subtotal and parsed lines differ.</Text>
                    </Card.Content>
                  </Card>
                )}
                {receipt.items.map((item, index) => (
                  <Card key={item.item_id} mode="elevated">
                    <Card.Content>
                      <View style={styles.itemHeader}>
                        <View style={styles.itemName}>
                          <Text variant="titleMedium">{item.name}</Text>
                          <Text variant="bodySmall" style={styles.mutedText}>
                            {Number.isInteger(item.qty) ? item.qty.toFixed(0) : item.qty} {item.unit}{' '}
                            · {item.raw_text}
                          </Text>
                        </View>
                        <Text variant="titleMedium" style={styles.price}>
                          ${item.line_total.toFixed(2)}
                        </Text>
                      </View>
                      {(item.storage || item.excluded) && <Divider style={styles.divider} />}
                      <View style={styles.chips}>
                        {item.storage && (
                          <Chip compact icon="fridge-outline">
                            {item.storage.method} · {item.storage.duration_days}d
                          </Chip>
                        )}
                        {item.excluded && <Chip compact>Ledger only</Chip>}
                        {item.needs_review && <Chip compact icon="alert">Check me</Chip>}
                      </View>
                    </Card.Content>
                  </Card>
                ))}
              </View>
            )}
          </ScrollView>
        </View>
      </PaperProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F8FAFC',
  },
  content: {
    width: '100%',
    maxWidth: 720,
    alignSelf: 'center',
    padding: 16,
    gap: 16,
  },
  mutedText: {
    color: '#64748B',
    marginTop: 4,
  },
  actions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    marginTop: 20,
  },
  preview: {
    width: '100%',
    height: 240,
    borderRadius: 12,
    backgroundColor: '#E2E8F0',
  },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  loadingCopy: {
    flex: 1,
    marginLeft: 16,
  },
  errorCard: {
    borderColor: '#DC2626',
  },
  warningCard: {
    borderColor: '#D97706',
  },
  results: {
    gap: 12,
  },
  itemHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  itemName: {
    flex: 1,
    paddingRight: 12,
  },
  price: {
    fontVariant: ['tabular-nums'],
  },
  divider: {
    marginVertical: 12,
  },
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
});
