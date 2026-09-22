import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import CameraForm from './CameraForm';
import { mockCamera, mockCameraPresets, mockCameraTestFailed, mockCameraTestOk } from '@/lib/gate-mock-data';

const meta: Meta<typeof CameraForm> = {
  title: 'Gate/CameraForm',
  component: CameraForm,
  tags: ['autodocs'],
  args: {
    presets: mockCameraPresets,
    onSave: fn(async () => {}),
    onTest: fn(async () => mockCameraTestOk),
    onCancel: fn(),
  },
};

export default meta;
type Story = StoryObj<typeof CameraForm>;

/** A new camera: vendor preset paths filled in, nothing tested yet. */
export const Empty: Story = {};

/** A saved camera: "password set" indicator, stored read zone. */
export const Saved: Story = {
  args: { camera: mockCamera },
};

/** Waiting for the camera to answer the connection test. */
export const Testing: Story = {
  args: { camera: mockCamera, initialTest: { state: 'testing' }, onTest: () => new Promise(() => {}) },
};

/** Authentication failed on the snapshot step. */
export const TestFailed: Story = {
  args: { camera: mockCamera, initialTest: { state: 'done', result: mockCameraTestFailed } },
};

/** Test passed; the read zone is being drawn on the snapshot. */
export const RoiEditing: Story = {
  args: { camera: mockCamera, initialTest: { state: 'done', result: mockCameraTestOk }, initialRoiEditing: true },
};
