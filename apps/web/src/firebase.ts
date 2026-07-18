import { getApp, getApps, initializeApp, type FirebaseApp } from "firebase/app";
import {
  connectAuthEmulator,
  getAuth,
  type Auth,
} from "firebase/auth";
import {
  connectFirestoreEmulator,
  doc,
  getDoc,
  getFirestore,
  initializeFirestore,
  persistentLocalCache,
  persistentMultipleTabManager,
  type Firestore,
} from "firebase/firestore";

import publicConfig from "../../../config/firebase-public-config.json";

export interface FirebaseServices {
  app: FirebaseApp;
  auth: Auth;
  db: Firestore;
}

let services: FirebaseServices | null | undefined;

function configFromEnvironment() {
  return {
    apiKey: (import.meta.env.VITE_FIREBASE_API_KEY as string | undefined) || publicConfig.apiKey,
    authDomain: (import.meta.env.VITE_FIREBASE_AUTH_DOMAIN as string | undefined) || publicConfig.authDomain,
    projectId: (import.meta.env.VITE_FIREBASE_PROJECT_ID as string | undefined) || publicConfig.projectId,
    appId: (import.meta.env.VITE_FIREBASE_APP_ID as string | undefined) || publicConfig.appId,
  };
}

export function firebaseIsConfigured(): boolean {
  const config = configFromEnvironment();
  return Boolean(config.apiKey && config.authDomain && config.projectId && config.appId);
}

export function getFirebaseServices(): FirebaseServices | null {
  if (services !== undefined) return services;
  if (!firebaseIsConfigured()) {
    services = null;
    return services;
  }

  const app = getApps().length ? getApp() : initializeApp(configFromEnvironment());
  let db: Firestore;
  try {
    db = initializeFirestore(app, {
      localCache: persistentLocalCache({ tabManager: persistentMultipleTabManager() }),
    });
  } catch {
    db = getFirestore(app);
  }
  const auth = getAuth(app);

  if (import.meta.env.VITE_USE_FIREBASE_EMULATORS === "true") {
    connectAuthEmulator(auth, "http://127.0.0.1:9099", { disableWarnings: true });
    connectFirestoreEmulator(db, "127.0.0.1", 8080);
  }
  services = { app, auth, db };
  return services;
}

export async function checkFirestoreConnection(ownerUid: string): Promise<void> {
  const current = getFirebaseServices();
  if (!current) throw new Error("FIREBASE_CONFIG_MISSING");
  await getDoc(doc(current.db, `users/${ownerUid}`));
}
