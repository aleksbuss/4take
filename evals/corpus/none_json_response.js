async function fetchProfile(client, id) {
  const res = await client.get(`/users/${id}`);
  const body = await res.json();
  return {
    name: body.profile.displayName.trim(),
    avatar: body.profile.avatarUrl || "/static/default.png",
  };
}
