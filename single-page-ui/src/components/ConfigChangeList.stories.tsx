import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import ConfigChangeList from './ConfigChangeList';
import { mockConfigChanges } from '@/lib/gate-mock-data';

const meta: Meta<typeof ConfigChangeList> = {
  title: 'Gate/ConfigChangeList',
  component: ConfigChangeList,
  tags: ['autodocs'],
};

export default meta;
type Story = StoryObj<typeof ConfigChangeList>;

export const Default: Story = { args: { changes: mockConfigChanges } };
export const Empty: Story = { args: { changes: [] } };
