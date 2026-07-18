import Constants from 'expo-constants';
import { Platform } from 'react-native';

import type {
  ConfirmReceiptResponse,
  DemoSeedResponse,
  InsightsResponse,
  MealConsumeResponse,
  MealsResponse,
  PantryResponse,
  ReceiptDraft,
  ReceiptItem,
  SpoilPantryResponse,
} from './types';

function inferredDevelopmentApiUrl(): string {
  const hostUri = Constants.expoConfig?.hostUri;
  const host = hostUri?.replace(/^https?:\/\//, '').split(':')[0];
  return host ? `http://${host}:8000` : 'http://127.0.0.1:8000';
}

export const API_URL = (
  process.env.EXPO_PUBLIC_API_URL ?? inferredDevelopmentApiUrl()
).replace(/\/$/, '');
const DEMO_TOKEN = process.env.EXPO_PUBLIC_DEMO_TOKEN ?? '';
const REQUEST_TIMEOUT_MS = 90_000;

interface UploadableImage {
  uri: string;
  mimeType: string;
  fileName: string;
}

interface ErrorEnvelope {
  error?: {
    user_message?: string;
  };
}

export class FreshLedgerApiError extends Error {
  constructor(public readonly userMessage: string) {
    super(userMessage);
    this.name = 'FreshLedgerApiError';
  }
}

export const ScanApiError = FreshLedgerApiError;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!DEMO_TOKEN) {
    throw new FreshLedgerApiError(
      'FreshLedger is missing EXPO_PUBLIC_DEMO_TOKEN. Add app/.env and restart Expo.',
    );
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${DEMO_TOKEN}`,
        ...init.headers,
      },
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      throw new FreshLedgerApiError(
        'The FreshLedger kitchen took too long to respond — please try again.',
      );
    }
    throw new FreshLedgerApiError(
      `Could not reach FreshLedger at ${API_URL}. Check the server and phone Wi-Fi.`,
    );
  } finally {
    clearTimeout(timer);
  }

  let payload: T | ErrorEnvelope | null = null;
  try {
    const body = await response.text();
    payload = body ? (JSON.parse(body) as T | ErrorEnvelope) : null;
  } catch {
    if (!response.ok) {
      throw new FreshLedgerApiError(
        'FreshLedger returned an unreadable error — please retry in a moment.',
      );
    }
    throw new FreshLedgerApiError('FreshLedger returned an unreadable response.');
  }

  if (!response.ok) {
    const envelope = payload as ErrorEnvelope | null;
    throw new FreshLedgerApiError(
      envelope?.error?.user_message ?? 'FreshLedger hit a snag — please try again.',
    );
  }
  if (payload === null) {
    throw new FreshLedgerApiError('FreshLedger returned an empty response.');
  }
  return payload as T;
}

export async function scanReceipt(image: UploadableImage): Promise<ReceiptDraft> {
  const form = new FormData();
  if (Platform.OS === 'web') {
    const imageResponse = await fetch(image.uri);
    const blob = await imageResponse.blob();
    form.append('image', blob, image.fileName);
  } else {
    form.append(
      'image',
      {
        uri: image.uri,
        name: image.fileName,
        type: image.mimeType,
      } as unknown as Blob,
    );
  }
  return request<ReceiptDraft>('/v1/receipts/scan', { method: 'POST', body: form });
}

export function scanDemoReceipt(sampleId: string): Promise<ReceiptDraft> {
  return request<ReceiptDraft>(`/v1/demo/receipts/${encodeURIComponent(sampleId)}/scan`, {
    method: 'POST',
  });
}

export function seedDemo(): Promise<DemoSeedResponse> {
  return request<DemoSeedResponse>('/v1/demo/seed', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile: 'judge' }),
  });
}

export function confirmReceipt(receipt: ReceiptDraft): Promise<ConfirmReceiptResponse> {
  return request<ConfirmReceiptResponse>(`/v1/receipts/${receipt.receipt_id}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      store_name: receipt.store_name,
      purchased_at: receipt.purchased_at,
      items: receipt.items.map((item: ReceiptItem) => ({
        item_id: item.item_id,
        name: item.name,
        qty: item.qty,
        unit: item.unit,
        unit_price: item.unit_price,
        category: item.category,
        excluded: item.excluded,
        storage_method_override: item.storage?.method ?? null,
      })),
    }),
  });
}

export function getPantry(): Promise<PantryResponse> {
  return request<PantryResponse>('/v1/pantry?status=active');
}

export function getMeals(): Promise<MealsResponse> {
  return request<MealsResponse>('/v1/meals/today');
}

export function getInsights(): Promise<InsightsResponse> {
  return request<InsightsResponse>('/v1/insights');
}

export function consumeMealItems(pantryItemIds: number[]): Promise<MealConsumeResponse> {
  return request<MealConsumeResponse>('/v1/meals/consume', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      items: pantryItemIds.map((pantryItemId) => ({
        pantry_item_id: pantryItemId,
        portion: 1,
      })),
    }),
  });
}

export function consumePantryItem(pantryItemId: number, portion = 1): Promise<unknown> {
  return request(`/v1/pantry/${pantryItemId}/consume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ portion }),
  });
}

export function spoilPantryItem(
  pantryItemId: number,
  portion = 1,
): Promise<SpoilPantryResponse> {
  return request<SpoilPantryResponse>(`/v1/pantry/${pantryItemId}/spoil`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ portion }),
  });
}
