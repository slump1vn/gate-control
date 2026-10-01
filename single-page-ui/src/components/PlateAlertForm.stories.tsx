import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import PlateAlertForm from './PlateAlertForm';
import { mockPlateAlerts } from '@/lib/gate-mock-data';

const meta: Meta<typeof PlateAlertForm> = {
  title: 'Gate/PlateAlertForm',
  component: PlateAlertForm,
  tags: ['autodocs'],
  args: { onSubmit: fn(async () => {}), onCancel: fn() },
  decorators: [(Story) => <div className="max-w-2xl"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof PlateAlertForm>;

export const New: Story = {};
export const Edit: Story = { args: { alert: mockPlateAlerts[1] } };
