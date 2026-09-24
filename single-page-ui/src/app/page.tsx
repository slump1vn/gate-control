'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import UploadForm from '@/components/UploadForm';
import { getImages, isUploadPublic } from '@/lib/api';
import type { ImageSummary } from '@/lib/api';
import ImageCard from '@/components/ImageCard';
import { useAuth } from '@/components/AuthContext';
import { useI18n } from '@/components/I18nContext';
import { useHealth } from '@/components/HealthContext';
import AvailabilityGraph from '@/components/AvailabilityGraph';
import Spinner from '@/components/Spinner';
import { cardClass, primaryButton } from '@/components/ui';

/** Visitors who are not signed in are shown the door, not the model. */
function SignedOut() {
  const { t } = useI18n();
  return (
    <div className="max-w-md mx-auto px-4 py-20 text-center">
      <h1 className="text-3xl font-light bg-gradient-to-r from-[#667eea] to-[#764ba2] bg-clip-text text-transparent mb-3">
        VietinBankSchool LPR
      </h1>
      <div className={`${cardClass} p-6`}>
        <p className="text-gray-600 dark:text-gray-400 mb-5">{t('home.intro')}</p>
        <Link href="/login" className={`${primaryButton} inline-block`}>{t('auth.signIn')}</Link>
      </div>
    </div>
  );
}

export default function HomePage() {
  const { t } = useI18n();
  const router = useRouter();
  const { loading, authenticated } = useAuth();
  const { isHealthy } = useHealth();
  const [publicUpload, setPublicUpload] = useState<boolean | null>(null);
  const [recentImages, setRecentImages] = useState<ImageSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [rateLimitError, setRateLimitError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => { isUploadPublic().then(setPublicUpload).catch(() => setPublicUpload(false)); }, []);

  const loadRecent = async () => {
    try {
      const data = await getImages({ page_size: 9 });
      setRecentImages(data.results);
    } catch {
    } finally {
      setLoaded(true);
    }
  };

  if (loading || publicUpload === null) return <Spinner className="py-24" />;
  // The service decides: with PUBLIC_UPLOAD_ENABLED off, the tool and the images
  // it produces need a login, and the API refuses anonymous calls either way.
  if (!authenticated && !publicUpload) return <SignedOut />;

  if (!loaded) loadRecent();

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <div className="text-center mb-8">
        <h1 className="text-4xl font-light bg-gradient-to-r from-[#667eea] to-[#764ba2] bg-clip-text text-transparent">
          VietinBankSchool LPR
        </h1>
        <p className="mt-2 text-gray-600 dark:text-gray-400">{t('home.uploadHint')}</p>
      </div>

      {rateLimitError && (
        <div className="max-w-2xl mx-auto mb-4 p-3 bg-amber-100 dark:bg-amber-900/30 border border-amber-300 dark:border-amber-700 rounded-lg text-amber-700 dark:text-amber-400 text-sm">
          {rateLimitError}
        </div>
      )}

      {error && (
        <div className="max-w-2xl mx-auto mb-4 p-3 bg-red-100 dark:bg-red-900/30 border border-red-300 dark:border-red-700 rounded-lg text-red-700 dark:text-red-400 text-sm">
          {error}
        </div>
      )}

      <div className="max-w-2xl mx-auto mb-12">
        <UploadForm
          onSuccess={(data) => {
            const id = data?.image_id || data?.id;
            if (id) router.push(`/image/${id}`);
          }}
          onError={(msg) => {
            if (msg.startsWith('__RATE_LIMIT__')) {
              const seconds = msg.replace('__RATE_LIMIT__', '');
              setRateLimitError(`Too many requests. Please wait ${seconds} seconds and try again.`);
              setError(null);
            } else {
              setError(msg);
              setRateLimitError(null);
            }
          }}
          onUploadStart={() => { setRateLimitError(null); setError(null); }}
          disabled={isHealthy === false}
        />
      </div>

      <div className="max-w-4xl mx-auto mb-12">
        <AvailabilityGraph />
      </div>

      {recentImages.length > 0 && (
        <div>
          <h2 className="text-xl font-semibold mb-4">Recent Uploads</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {recentImages.map((img) => (
              <ImageCard key={img.id} image={img} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
