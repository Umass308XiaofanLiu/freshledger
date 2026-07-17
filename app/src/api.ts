import { Platform } from 'react-native';

import type { ReceiptDraft } from './types';

const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? 'http://127.0.0.1:8000').replace(
  /\/$/,
  '',
);
const DEMO_TOKEN = process.env.EXPO_PUBLIC_DEMO_TOKEN ?? '';

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

export class ScanApiError extends Error {
  constructor(public readonly userMessage: string) {
    super(userMessage);
  }
}

export async function scanReceipt(image: UploadableImage): Promise<ReceiptDraft> {
  if (!DEMO_TOKEN) {
    throw new ScanApiError(
      'FreshLedger is missing EXPO_PUBLIC_DEMO_TOKEN. Add app/.env and restart Expo.',
    );
  }

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

  let response: Response;
  try {
    response = await fetch(`${API_URL}/v1/receipts/scan`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${DEMO_TOKEN}` },
      body: form,
    });
  } catch {
    throw new ScanApiError(
      `Could not reach FreshLedger at ${API_URL}. Check the server and phone Wi-Fi.`,
    );
  }

  const payload = (await response.json()) as ReceiptDraft | ErrorEnvelope;
  if (!response.ok) {
    const envelope = payload as ErrorEnvelope;
    throw new ScanApiError(
      envelope.error?.user_message ?? 'The receipt reader hiccuped — please try scanning again.',
    );
  }
  return payload as ReceiptDraft;
}

