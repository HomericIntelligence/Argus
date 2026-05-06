package store

import (
	"testing"

	"github.com/HomericIntelligence/atlas/internal/catalog"
	"github.com/HomericIntelligence/atlas/internal/tailscale"
)

func TestHostServices_FiltersByHost(t *testing.T) {
	c := NewCache()
	c.SetProbes([]catalog.ProbeResult{
		{Host: "alpha", ServiceDef: catalog.ServiceDef{Name: "agamemnon"}, OK: true},
		{Host: "alpha", ServiceDef: catalog.ServiceDef{Name: "nestor"}, OK: false},
		{Host: "beta", ServiceDef: catalog.ServiceDef{Name: "agamemnon"}, OK: true},
	})
	got := c.HostServices("alpha")
	if len(got) != 2 {
		t.Fatalf("HostServices(alpha): got %d, want 2", len(got))
	}
	for _, r := range got {
		if r.Host != "alpha" {
			t.Errorf("HostServices(alpha) returned probe for host %q", r.Host)
		}
	}
}

func TestHostServices_NoMatch_ReturnsEmpty(t *testing.T) {
	c := NewCache()
	c.SetProbes([]catalog.ProbeResult{{Host: "alpha", ServiceDef: catalog.ServiceDef{Name: "agamemnon"}, OK: true}})
	got := c.HostServices("nonexistent")
	if len(got) != 0 {
		t.Errorf("HostServices(nonexistent): got %d, want 0", len(got))
	}
}

func TestBuildHostViews_EmptyCache(t *testing.T) {
	c := NewCache()
	got := BuildHostViews(c)
	if len(got) != 0 {
		t.Errorf("BuildHostViews on empty cache: got %d views, want 0", len(got))
	}
}

func TestBuildHostViews_DevicesWithoutProbes(t *testing.T) {
	c := NewCache()
	c.SetDevices([]tailscale.Device{
		{Hostname: "alpha", TailscaleIP: "100.64.0.1", Online: true},
		{Hostname: "beta", TailscaleIP: "100.64.0.2", Online: false},
	})
	got := BuildHostViews(c)
	if len(got) != 2 {
		t.Fatalf("BuildHostViews: got %d, want 2", len(got))
	}
	for _, v := range got {
		if len(v.Services) != 0 {
			t.Errorf("host %s should have 0 services with no probes set, got %d", v.Hostname, len(v.Services))
		}
	}
}

func TestBuildHostViews_JoinsDevicesAndProbes(t *testing.T) {
	c := NewCache()
	c.SetDevices([]tailscale.Device{
		{Hostname: "alpha", TailscaleIP: "100.64.0.1", Online: true},
		{Hostname: "beta", TailscaleIP: "100.64.0.2", Online: true},
	})
	c.SetProbes([]catalog.ProbeResult{
		{Host: "alpha", ServiceDef: catalog.ServiceDef{Name: "agamemnon"}, URL: "http://alpha:8080", OK: true},
		{Host: "alpha", ServiceDef: catalog.ServiceDef{Name: "nestor"}, URL: "http://alpha:8081", OK: true},
		{Host: "beta", ServiceDef: catalog.ServiceDef{Name: "agamemnon"}, URL: "http://beta:8080", OK: false},
	})

	views := BuildHostViews(c)
	if len(views) != 2 {
		t.Fatalf("BuildHostViews: got %d, want 2", len(views))
	}

	// Devices preserve order.
	if views[0].Hostname != "alpha" || views[1].Hostname != "beta" {
		t.Errorf("BuildHostViews: device order changed; got %s, %s", views[0].Hostname, views[1].Hostname)
	}

	// alpha gets two services.
	if len(views[0].Services) != 2 {
		t.Errorf("alpha views: got %d services, want 2", len(views[0].Services))
	}
	// beta gets one service.
	if len(views[1].Services) != 1 {
		t.Errorf("beta views: got %d services, want 1", len(views[1].Services))
	}
	// IPs and online state propagate.
	if views[0].TailscaleIP != "100.64.0.1" || !views[0].Online {
		t.Errorf("alpha view metadata wrong: %+v", views[0])
	}
}

func TestBuildHostViews_DefensiveOnDeviceMutation(t *testing.T) {
	c := NewCache()
	c.SetDevices([]tailscale.Device{{Hostname: "alpha", TailscaleIP: "100.64.0.1", Online: true}})
	views := BuildHostViews(c)
	views[0].Hostname = "MUTATED"
	again := BuildHostViews(c)
	if again[0].Hostname == "MUTATED" {
		t.Error("BuildHostViews returned a slice that aliases cache state; mutation leaked")
	}
}
