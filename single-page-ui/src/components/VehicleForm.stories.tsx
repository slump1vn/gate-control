import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import VehicleForm from './VehicleForm';
import type { PlatePreview } from '@/lib/gate-api';
import { mockVehicle } from '@/lib/gate-mock-data';

// Rough stand-in for the server's normaliser, for the live preview.
const preview = async (plate: string): Promise<PlatePreview> => {
  const normalized = plate.toUpperCase().replace(/Đ/g, 'D').replace(/[^A-Z0-9]/g, '');
  return { plate, normalized, existing_vehicle: normalized === '30A12345' ? { id: 1, plate_display: '30A-123.45' } : null };
};

const meta: Meta<typeof VehicleForm> = {
  title: 'Gate/VehicleForm',
  component: VehicleForm,
  tags: ['autodocs'],
  args: { onSubmit: fn(async () => {}), onCancel: fn(), preview },
  decorators: [(Story) => <div className="max-w-2xl"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof VehicleForm>;

export const New: Story = {};
export const Edit: Story = { args: { vehicle: mockVehicle } };
