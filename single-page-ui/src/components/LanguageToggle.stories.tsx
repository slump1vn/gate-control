import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import LanguageToggle from './LanguageToggle';
import GateStatusCard from './GateStatusCard';
import { I18nProvider } from './I18nContext';
import { mockGateStatusSimulated } from '@/lib/gate-mock-data';

const meta: Meta<typeof LanguageToggle> = {
  title: 'Components/LanguageToggle',
  component: LanguageToggle,
  tags: ['autodocs'],
  decorators: [(Story) => <div className="bg-gray-800 p-4 inline-block rounded-md"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof LanguageToggle>;

/** The button shows the language in use; clicking it switches to the other one. */
export const Vietnamese: Story = {
  decorators: [(Story) => <I18nProvider initial="vi"><Story /></I18nProvider>],
};

export const English: Story = {
  decorators: [(Story) => <I18nProvider initial="en"><Story /></I18nProvider>],
};

/** A whole card in each language: Vietnamese wording is longer and must still fit. */
export const InContext: Story = {
  render: () => (
    <div className="flex flex-col gap-4 bg-white dark:bg-black p-4 max-w-xl">
      {(['vi', 'en'] as const).map((lang) => (
        <I18nProvider key={lang} initial={lang}>
          <div className="space-y-2">
            <div className="bg-gray-800 inline-block rounded-md"><LanguageToggle /></div>
            <GateStatusCard gate={mockGateStatusSimulated} mode="shadow" onCommand={async () => {}} />
          </div>
        </I18nProvider>
      ))}
    </div>
  ),
};
