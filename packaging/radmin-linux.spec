Name:           radmin-linux
Version:        0.7.0
Release:        1%{?dist}
Summary:        Radmin VPN on Linux (native OpenRad engine or a headless VM)

License:        MIT
URL:            https://github.com/gabrielmf1998/Radmin-Linux
BuildArch:      noarch

Requires:       curl
Requires:       iproute
Requires:       polkit
Suggests:       qemu-system-x86-core
Suggests:       qemu-img
Suggests:       zstd
Suggests:       NetworkManager
Suggests:       socat
Requires:       python3 >= 3.11
Requires:       python3-pip
Recommends:     tigervnc

%description
Radmin VPN (Linux) drives the real Radmin VPN running inside a tiny headless
Windows VM, from a native Qt front-end. The user never opens the VM: the app
powers it on/off, shows the mesh, connects/disconnects and self-heals.
This package installs the front-end, agent scripts and the installer helper.
The VM image is provided separately (it is large and carries state).

%prep
# sources are laid down by the build script into %{_builddir}

%install
rm -rf %{buildroot}
install -d %{buildroot}%{_datadir}/radmin-linux
cp -r %{srcdir}/app %{srcdir}/shim %{srcdir}/agent %{buildroot}%{_datadir}/radmin-linux/
find %{buildroot}%{_datadir}/radmin-linux -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
install -m644 %{srcdir}/env.sh %{srcdir}/OPENRAD_VERSION %{buildroot}%{_datadir}/radmin-linux/
install -Dm644 %{srcdir}/packaging/polkit/49-radmin-linux-openrad.rules.in %{buildroot}%{_datadir}/radmin-linux/packaging/polkit/49-radmin-linux-openrad.rules.in
install -m755 %{srcdir}/preflight.sh %{srcdir}/radmin-linux.sh %{srcdir}/uninstall.sh \
        %{srcdir}/deploy-agent.sh %{srcdir}/install.sh %{srcdir}/build-vm.sh \
        %{buildroot}%{_datadir}/radmin-linux/
install -d %{buildroot}%{_datadir}/radmin-linux/vm
install -m755 %{srcdir}/vm/*.sh %{buildroot}%{_datadir}/radmin-linux/vm/
install -d %{buildroot}%{_bindir}
ln -s %{_datadir}/radmin-linux/radmin-linux.sh %{buildroot}%{_bindir}/radmin-linux
install -d %{buildroot}%{_datadir}/applications
install -m644 %{srcdir}/packaging/radmin-linux.desktop %{buildroot}%{_datadir}/applications/
for sz in 16 22 24 32 48 64 128 256; do
  install -Dm644 %{srcdir}/assets/radmin-linux-$sz.png \
    %{buildroot}%{_datadir}/icons/hicolor/${sz}x${sz}/apps/radmin-linux.png
done

%files
%{_datadir}/radmin-linux/
%{_bindir}/radmin-linux
%{_datadir}/applications/radmin-linux.desktop
%{_datadir}/icons/hicolor/*/apps/radmin-linux.png

%post
echo "Radmin-Linux installed. Finish the per-user setup with:"
echo "  %{_datadir}/radmin-linux/install.sh      (native engine, OpenRad)"
echo "  or RADMIN_ENGINE=vm %{_datadir}/radmin-linux/install.sh /path/to/radmin-linux-base.qcow2.zst"
echo "then launch it from the menu, or run: radmin-linux"

%changelog
* Wed Nov 25 2026 gabrielmf1998 <110578985+gabrielmf1998@users.noreply.github.com> - 0.7.0-1
- Native engine: network admin (delete, kick, admins), block an IP, relays only, retry peers

* Wed Nov 25 2026 gabrielmf1998 <110578985+gabrielmf1998@users.noreply.github.com> - 0.6.0-1
- Native engine (OpenRad), no VM needed

* Tue Nov 24 2026 gabrielmf1998 <110578985+gabrielmf1998@users.noreply.github.com> - 0.5.3-1
- Quit turns the VM off, closes the VM viewers, then exits

* Tue Nov 24 2026 gabrielmf1998 <110578985+gabrielmf1998@users.noreply.github.com> - 0.5.2-1
- Shows "Shutting down" while the VM turns off instead of "Starting"

* Tue Nov 24 2026 gabrielmf1998 <110578985+gabrielmf1998@users.noreply.github.com> - 0.5.1-1
- List shown only after a complete reading; VM closes the activation dialog at logon

* Tue Nov 24 2026 gabrielmf1998 <110578985+gabrielmf1998@users.noreply.github.com> - 0.5.0-1
- Member list read from the Radmin window, online state from Radmin itself

* Tue Nov 24 2026 gabrielmf1998 <110578985+gabrielmf1998@users.noreply.github.com> - 0.4.0-1
- In-app updates, tray status, ping times, lighter refresh, single instance

* Tue Nov 24 2026 gabrielmf1998 <110578985+gabrielmf1998@users.noreply.github.com> - 0.3.0-1
- Own Radmin identity per install, agent API, lighter VM, safer installer

* Wed Sep 02 2026 gabrielmf1998 <110578985+gabrielmf1998@users.noreply.github.com> - 0.1.0-1
- First packaged release
