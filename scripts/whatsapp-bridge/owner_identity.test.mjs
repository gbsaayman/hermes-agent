import test from 'node:test';
import assert from 'node:assert/strict';

import { isConfiguredOwnerDm } from './owner_identity.js';

function matchExact(senderId, users) {
  return users.has(senderId);
}

test('configured owner DM is elevated', () => {
  assert.equal(isConfiguredOwnerDm({
    senderId: '27830000001',
    ownerUsers: new Set(['27830000001']),
    matchesUser: matchExact,
  }), true);
});

test('ordinary allowlisted user is not elevated', () => {
  assert.equal(isConfiguredOwnerDm({
    senderId: '27830000002',
    ownerUsers: new Set(['27830000001']),
    matchesUser: matchExact,
  }), false);
});

test('owner identity does not elevate group traffic', () => {
  assert.equal(isConfiguredOwnerDm({
    isGroup: true,
    senderId: '27830000001',
    ownerUsers: new Set(['27830000001']),
    matchesUser: matchExact,
  }), false);
});

test('owner identity does not elevate fromMe traffic', () => {
  assert.equal(isConfiguredOwnerDm({
    fromMe: true,
    senderId: '27830000001',
    ownerUsers: new Set(['27830000001']),
    matchesUser: matchExact,
  }), false);
});

test('wildcard owner configuration fails closed', () => {
  assert.equal(isConfiguredOwnerDm({
    senderId: '27830000002',
    ownerUsers: new Set(['*']),
    matchesUser: () => true,
  }), false);
});
